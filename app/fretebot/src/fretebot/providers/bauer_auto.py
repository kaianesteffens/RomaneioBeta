"""Provider BAUER - automação web via Playwright."""
from __future__ import annotations

from datetime import datetime
from typing import Optional
import asyncio
import re
import random
import unicodedata
import httpx

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

from fretebot.providers.base import ProviderBase
from fretebot.models import Cotacao
from fretebot.logging_conf import get_logger

logger = get_logger(__name__)


class BauerAutoProvider(ProviderBase):
    """Provider BAUER usando automação Playwright."""

    def __init__(
        self,
        cotacao_url: str,
        cnpj_pagador: str,
        cnpj_remetente: str,
        cnpj_destinatario: str,
        headless: bool = True,
        timeout_ms: int = 60000,
        numero_nf: str = "1",
        quantidade: int = 1,
        altura_m: float = 0.0,
        largura_m: float = 0.0,
        profundidade_m: float = 0.0,
        cubagens: Optional[list[dict]] = None,
    ):
        super().__init__(nome="BAUER")
        self.cotacao_url = cotacao_url
        self.cnpj_pagador = self._somente_digitos(cnpj_pagador)
        self.cnpj_remetente = self._somente_digitos(cnpj_remetente)
        self.cnpj_destinatario = self._somente_digitos(cnpj_destinatario)
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.numero_nf = numero_nf
        self.quantidade = quantidade
        self.altura_m = altura_m
        self.largura_m = largura_m
        self.profundidade_m = profundidade_m
        self.cubagens = self._normalizar_cubagens_m(cubagens)
        self._zip_codes_cache: dict[str, str] | None = None
        self.last_error: str | None = None
        self._logged_in = False

    @staticmethod
    def _somente_digitos(valor: str) -> str:
        return re.sub(r"\D", "", valor or "")

    @staticmethod
    def _normalizar_cubagens_m(cubagens: Optional[list[dict]]) -> list[dict]:
        validas: list[dict] = []
        if not isinstance(cubagens, list):
            return validas
        for row in cubagens:
            if not isinstance(row, dict):
                continue
            try:
                qtd = int(row.get("quantidade", 0) or 0)
                alt = float(row.get("altura_m", 0) or 0)
                larg = float(row.get("largura_m", 0) or 0)
                prof = float(
                    row.get("profundidade_m", row.get("comprimento_m", 0)) or 0
                )
            except Exception:
                continue
            if qtd <= 0 or alt <= 0 or larg <= 0 or prof <= 0:
                continue
            validas.append(
                {
                    "quantidade": qtd,
                    "altura_m": alt,
                    "largura_m": larg,
                    "profundidade_m": prof,
                }
            )
        return validas

    async def pre_login(self):
        """BAUER não requer login - apenas pré-carrega zip codes."""
        self._logged_in = True
        logger.info(f"[{self.nome}] Pronto (sem login necessário)")

    async def cleanup(self):
        """Nenhum recurso persistente para fechar."""
        self._logged_in = False

    async def _fill_field(self, page, labels: list[str], value: str) -> bool:
        for label in labels:
            try:
                locator = page.get_by_label(re.compile(label, re.IGNORECASE)).first
                if await locator.count() > 0:
                    await locator.click()
                    await locator.fill("")
                    await locator.type(value)
                    return True
            except Exception:
                pass

            try:
                input_by_label = page.locator(
                    f"xpath=//label[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{label.lower()}')]/following::*[self::input or self::textarea][1]"
                ).first
                if await input_by_label.count() > 0:
                    await input_by_label.click()
                    await input_by_label.fill("")
                    await input_by_label.type(value)
                    return True
            except Exception:
                pass
        return False

    async def _select_or_fill(self, page, labels: list[str], value: str) -> bool:
        if await self._fill_field(page, labels, value):
            return True

        for label in labels:
            try:
                select_like = page.locator(
                    f"xpath=//label[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{label.lower()}')]/following::*[@role='combobox' or contains(@class,'select')][1]"
                ).first
                if await select_like.count() > 0:
                    await select_like.click()
                    await page.keyboard.type(value)
                    await page.keyboard.press("Enter")
                    return True
            except Exception:
                pass
        return False

    async def _select_dropdown_option(
        self,
        page,
        field_name: str,
        labels: list[str],
        preferred_patterns: list[str] | None = None,
    ) -> bool:
        preferred_patterns = preferred_patterns or []

        anchors = [page.locator(f"#mui-component-select-{field_name}").first]
        for label in labels:
            anchors.append(
                page.locator(
                    f"xpath=//label[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{label.lower()}')]/following::*[@role='button' or @aria-haspopup='listbox'][1]"
                ).first
            )

        for anchor in anchors:
            try:
                if await anchor.count() == 0:
                    continue
                await anchor.click()
                await page.wait_for_timeout(200)

                options = page.get_by_role("option")
                count = await options.count()
                if count == 0:
                    await page.keyboard.press("Escape")
                    continue

                chosen = None
                for patt in preferred_patterns:
                    regex = re.compile(patt, re.IGNORECASE)
                    for idx in range(count):
                        text = (await options.nth(idx).inner_text()).strip()
                        if text and "selecione" not in text.lower() and regex.search(text):
                            chosen = options.nth(idx)
                            break
                    if chosen is not None:
                        break

                if chosen is None:
                    for idx in range(count):
                        text = (await options.nth(idx).inner_text()).strip()
                        if text and "selecione" not in text.lower():
                            chosen = options.nth(idx)
                            break

                if chosen is None:
                    await page.keyboard.press("Escape")
                    continue

                await chosen.click()
                return True
            except Exception:
                try:
                    await page.keyboard.press("Escape")
                except Exception:
                    pass
        return False

    async def _campos_obrigatorios_pendentes(self, page) -> bool:
        return await page.evaluate(
            """() => {
                const missing = document.querySelectorAll(
                    "input[required][value='none'], input[name='typeFreight'][value='none'], input[name='merchandise'][value='none']"
                );
                return missing.length > 0;
            }"""
        )

    async def _interacao_humana(self, page) -> None:
        await page.wait_for_timeout(random.randint(100, 250))
        await page.mouse.move(random.randint(220, 520), random.randint(120, 260), steps=5)
        await page.wait_for_timeout(random.randint(80, 200))

    async def _fill_input_by_name(self, page, name: str, value: str) -> bool:
        try:
            locator = page.locator(f'input[name="{name}"]').first
            if await locator.count() == 0:
                return False
            await locator.click()
            await locator.fill("")
            await locator.type(value)
            await locator.press("Tab")
            await page.wait_for_timeout(50)
            return True
        except Exception:
            return False

    async def _fill_input_by_name_index(self, page, name: str, value: str, index: int) -> bool:
        try:
            all_loc = page.locator(f'input[name="{name}"]')
            if await all_loc.count() <= index:
                return False
            locator = all_loc.nth(index)
            await locator.click()
            await locator.fill("")
            await locator.type(value)
            await locator.press("Tab")
            await page.wait_for_timeout(50)
            return True
        except Exception:
            return False

    async def _garantir_linha_volume(self, page, index: int) -> bool:
        """Garante que a linha de volume de índice `index` exista no formulário."""
        if index <= 0:
            return True
        try:
            if await page.locator('input[name="height"]').count() > index:
                return True
        except Exception:
            pass

        for _ in range(6):
            clicked = False
            try:
                plus_btn = page.get_by_role("button", name=re.compile(r"^\+$")).first
                if await plus_btn.count() > 0:
                    await plus_btn.click()
                    clicked = True
            except Exception:
                clicked = False
            if not clicked:
                try:
                    plus_btn = page.locator("button").filter(has_text="+").first
                    if await plus_btn.count() > 0:
                        await plus_btn.click()
                        clicked = True
                except Exception:
                    clicked = False
            if not clicked:
                return False
            await page.wait_for_timeout(200)
            try:
                if await page.locator('input[name="height"]').count() > index:
                    return True
            except Exception:
                pass

        return False

    async def _select_zip_code(self, page, index: int, cep: str) -> bool:
        try:
            combo_inputs = page.locator("input[id^='react-select-'][id$='-input']")
            if await combo_inputs.count() <= index:
                return False

            target = combo_inputs.nth(index)
            cleaned_cep = self._somente_digitos(cep)

            await target.click()
            await target.fill("")
            await target.type(cleaned_cep)
            await page.wait_for_timeout(300)

            options = page.locator("[id^='react-select-'][id*='-option-']")
            # Esperar opções aparecerem (até 3s)
            for _ in range(6):
                count = await options.count()
                if count > 0:
                    break
                await page.wait_for_timeout(500)
            count = await options.count()
            chosen = None

            for option_index in range(count):
                option_text = (await options.nth(option_index).inner_text()).strip()
                if cleaned_cep and cleaned_cep in self._somente_digitos(option_text):
                    chosen = options.nth(option_index)
                    break

            if chosen is not None:
                await chosen.click()
                return True

            await page.keyboard.press("Escape")
            return False
        except Exception:
            return False

    def _load_zip_codes(self) -> dict[str, str]:
        if self._zip_codes_cache is not None:
            return self._zip_codes_cache

        url = "https://admin.bauerexpress.com.br/api/page-digital-service/zips-codes"
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                response = httpx.get(url, timeout=30, follow_redirects=True)
                response.raise_for_status()
                payload = response.json()
                codes = payload.get("codes", [])
                self._zip_codes_cache = {
                    str(item.get("id", "")): str(item.get("name", ""))
                    for item in codes
                    if item.get("id")
                }
                logger.info(f"[{self.nome}] Malha BAUER carregada: {len(self._zip_codes_cache)} códigos")
                return self._zip_codes_cache
            except Exception as error:
                last_err = error
                logger.warning(
                    f"[{self.nome}] Tentativa {attempt+1}/3 falhou ao carregar CEPs BAUER: "
                    f"{type(error).__name__}: {error}"
                )
                if attempt < 2:
                    import time; time.sleep(2 * (attempt + 1))

        logger.error(f"[{self.nome}] Não foi possível carregar malha de CEPs BAUER após 3 tentativas: {last_err}")
        self._zip_codes_cache = {}
        return self._zip_codes_cache

    @staticmethod
    def _normalizar_texto(valor: str) -> str:
        texto = unicodedata.normalize("NFKD", valor or "")
        texto = "".join(char for char in texto if not unicodedata.combining(char))
        texto = re.sub(r"[^a-zA-Z0-9]", " ", texto)
        return re.sub(r"\s+", " ", texto).strip().lower()

    def _consultar_cidade_por_cep(self, cep: str) -> str | None:
        """Consulta o nome da cidade via ViaCEP a partir de um CEP."""
        digits = self._somente_digitos(cep)
        if len(digits) != 8:
            return None
        try:
            response = httpx.get(f"https://viacep.com.br/ws/{digits}/json/", timeout=10)
            response.raise_for_status()
            data = response.json()
            if data.get("erro"):
                return None
            cidade = data.get("localidade", "")
            if cidade:
                logger.info(f"[{self.nome}] CEP {digits} → cidade: {cidade}")
            return cidade or None
        except Exception as error:
            logger.warning(f"[{self.nome}] Falha ao consultar ViaCEP para {digits}: {error}")
            return None

    def _resolver_zip_code(self, consulta: str) -> str | None:
        codes = self._load_zip_codes()
        if not codes:
            return None

        digits = self._somente_digitos(consulta)
        if digits:
            if digits in codes:
                return digits

            # Tenta prefixos decrescentes (8→3 dígitos) para encontrar a cidade mais próxima
            for prefix_len in range(min(len(digits), 8), 2, -1):
                prefix = digits[:prefix_len]
                prefix_matches = [zip_code for zip_code in codes if zip_code.startswith(prefix)]
                if len(prefix_matches) == 1:
                    return prefix_matches[0]
                if len(prefix_matches) > 1:
                    prefix_matches.sort(key=lambda z: abs(int(z) - int(digits.ljust(8, '0')[:8])))
                    return prefix_matches[0]

            # Fallback: consulta a cidade pelo CEP e busca pelo nome na malha BAUER
            cidade = self._consultar_cidade_por_cep(digits)
            if cidade:
                resultado = self._buscar_por_nome(cidade, codes)
                if resultado:
                    logger.info(f"[{self.nome}] CEP {digits} resolvido pela cidade '{cidade}' → código {resultado}")
                    return resultado

        texto = self._normalizar_texto(consulta)
        if not texto:
            return None

        return self._buscar_por_nome(consulta, codes)

    def _buscar_por_nome(self, consulta: str, codes: dict[str, str]) -> str | None:
        """Busca um código BAUER pelo nome da cidade/descrição."""
        texto = self._normalizar_texto(consulta)
        if not texto:
            return None

        nome_matches: list[str] = []
        for zip_code, descricao in codes.items():
            descricao_normalizada = self._normalizar_texto(descricao)
            if texto in descricao_normalizada:
                nome_matches.append(zip_code)

        if len(nome_matches) == 1:
            return nome_matches[0]
        if len(nome_matches) > 1:
            logger.warning(
                f"[{self.nome}] Consulta '{consulta}' corresponde a múltiplos destinos; usando {nome_matches[0]}"
            )
            return nome_matches[0]

        return None

    async def _captcha_pendente(self, page) -> bool:
        return await page.evaluate(
            """() => {
                const token = document.querySelector("textarea[name='g-recaptcha-response']");
                const hasIframe = !!document.querySelector("iframe[src*='recaptcha']");
                if (!hasIframe) return false;
                if (!token) return true;
                return !token.value || token.value.trim().length < 20;
            }"""
        )

    async def _extrair_resultado(self, page) -> Optional[tuple[float, int]]:
        texto = await page.inner_text("body")

        include_keywords = ["cotação", "cotacao", "valor", "frete", "total", "preço", "preco"]
        exclude_keywords = [
            "reentrega",
            "tae",
            "tzr",
            "taxa",
            "mínimo",
            "minimo",
            "icms",
            "km rodado",
            "notas fiscais",
            "envelope",
        ]

        valor_frete = None
        for match in re.finditer(r"R\$\s*([\d.,]+)", texto, re.IGNORECASE):
            inicio = max(0, match.start() - 160)
            fim = min(len(texto), match.end() + 160)
            contexto = texto[inicio:fim].lower()

            if any(palavra in contexto for palavra in exclude_keywords):
                continue
            if not any(palavra in contexto for palavra in include_keywords):
                continue

            bruto = match.group(1)
            if "," in bruto:
                valor_frete = float(bruto.replace(".", "").replace(",", "."))
            else:
                valor_frete = float(bruto.replace(",", ""))
            break

        if valor_frete is None:
            return None

        prazo_dias = 0
        prazo_match = re.search(r"previs[aã]o\s*de\s*entrega[^\d]*(\d+)\s*(?:dia|dias)", texto, re.IGNORECASE)
        if not prazo_match:
            prazo_match = re.search(r"(\d+)\s*(?:dia|dias)", texto, re.IGNORECASE)
        if prazo_match:
            prazo_dias = int(prazo_match.group(1))

        return valor_frete, prazo_dias

    async def _cotar_async(self, origem: str, destino: str, peso: float, valor: float, tipo_frete: str = "cif") -> Optional[Cotacao]:
        # Pré-carrega CEPs em thread separada enquanto browser abre
        loop = asyncio.get_event_loop()
        zip_future = loop.run_in_executor(None, self._load_zip_codes)

        async with async_playwright() as playwright:
            from fretebot.providers.base import launch_browser_resilient
            browser = await launch_browser_resilient(
                playwright,
                headless=self.headless,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            )
            context_kwargs = {
                "viewport": {"width": 1600, "height": 1000},
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "locale": "pt-BR",
                "timezone_id": "America/Sao_Paulo",
            }

            context = await browser.new_context(**context_kwargs)
            page = await context.new_page()
            page.set_default_timeout(self.timeout_ms)

            quotation_responses: list[dict] = []

            async def _capture_quotation_response(response) -> None:
                if "page-digital-service-quotation/webservice/quotation" not in response.url:
                    return
                try:
                    data = await response.json()
                    if isinstance(data, dict):
                        quotation_responses.append(data)
                except Exception:
                    pass

            page.on("response", lambda response: asyncio.create_task(_capture_quotation_response(response)))

            try:
                # Inicia goto em paralelo enquanto espera CEPs carregarem
                goto_task = asyncio.create_task(page.goto(self.cotacao_url, wait_until="domcontentloaded"))

                # Espera CEPs (que pode já estar em cache)
                await zip_future
                origem_resolvida = self._resolver_zip_code(origem)
                if origem_resolvida is None:
                    self.last_error = f"Origem fora da cobertura BAUER: {origem}"
                    logger.error(f"[{self.nome}] {self.last_error}")
                    return None

                destino_resolvido = self._resolver_zip_code(destino)
                if destino_resolvido is None:
                    self.last_error = f"Destino fora da cobertura BAUER: {destino}"
                    logger.error(f"[{self.nome}] {self.last_error}")
                    return None

                # Aguarda página terminar de carregar (goto foi iniciado em paralelo)
                logger.info(f"[{self.nome}] Aguardando página de cotação")
                await goto_task
                # Espera o formulário estar pronto (input payerDocument visível)
                try:
                    await page.locator('input[name="payerDocument"]').wait_for(state="visible", timeout=10000)
                except PlaywrightTimeoutError:
                    pass

                await self._fill_input_by_name(page, "payerDocument", self.cnpj_pagador)
                await self._fill_input_by_name(page, "senderDocument", self.cnpj_remetente)
                await self._fill_input_by_name(page, "recipientDocument", self.cnpj_destinatario)
                await self._fill_input_by_name(page, "invoiceValue", f"{valor:.2f}".replace(".", ","))

                _frete_patterns = [r"^fob$", "fob"] if tipo_frete.lower() == "fob" else [r"^cif$", "cif"]
                await self._select_dropdown_option(page, "typeFreight", ["tipo do frete", "frete"], _frete_patterns)
                await self._select_dropdown_option(page, "merchandise", ["mercadoria"], [r"^express$", "express"])

                await self._fill_input_by_name(page, "weight", f"{peso:.2f}".replace(".", ","))

                cubagens = self._normalizar_cubagens_m(self.cubagens)
                if not cubagens:
                    if (
                        int(self.quantidade or 0) <= 0
                        or float(self.altura_m or 0) <= 0
                        or float(self.largura_m or 0) <= 0
                        or float(self.profundidade_m or 0) <= 0
                    ):
                        self.last_error = (
                            "Cubagem inválida: BAUER requer quantidade e dimensões reais do romaneio"
                        )
                        logger.error(f"[{self.nome}] {self.last_error}")
                        return None
                    cubagens = [
                        {
                            "quantidade": int(self.quantidade),
                            "altura_m": float(self.altura_m),
                            "largura_m": float(self.largura_m),
                            "profundidade_m": float(self.profundidade_m),
                        }
                    ]

                for idx, cub in enumerate(cubagens):
                    if idx > 0 and not await self._garantir_linha_volume(page, idx):
                        self.last_error = (
                            f"Não foi possível adicionar linha de volume #{idx + 1} na BAUER"
                        )
                        logger.error(f"[{self.nome}] {self.last_error}")
                        return None
                    ok_q = await self._fill_input_by_name_index(
                        page, "quantity", str(int(cub["quantidade"])), idx
                    )
                    ok_h = await self._fill_input_by_name_index(
                        page, "height", f"{float(cub['altura_m']):.2f}".replace(".", ","), idx
                    )
                    ok_w = await self._fill_input_by_name_index(
                        page, "width", f"{float(cub['largura_m']):.2f}".replace(".", ","), idx
                    )
                    ok_d = await self._fill_input_by_name_index(
                        page, "depth", f"{float(cub['profundidade_m']):.2f}".replace(".", ","), idx
                    )
                    if not (ok_q and ok_h and ok_w and ok_d):
                        self.last_error = (
                            f"Falha ao preencher cubagem #{idx + 1} na BAUER"
                        )
                        logger.error(f"[{self.nome}] {self.last_error}")
                        return None
                await self._fill_input_by_name(page, "observation", self.numero_nf)

                origem_ok = await self._select_zip_code(page, 0, origem_resolvida)
                destino_ok = await self._select_zip_code(page, 1, destino_resolvido)
                if not origem_ok or not destino_ok:
                    self.last_error = "Não foi possível selecionar CEP origem/destino na lista da BAUER"
                    logger.error(f"[{self.nome}] {self.last_error}")
                    return None

                try:
                    await page.get_by_role("radio", name=re.compile("por volume", re.IGNORECASE)).check()
                except Exception:
                    pass

                logger.info(f"[{self.nome}] Enviando cálculo")
                await page.get_by_role("button", name=re.compile("calcular", re.IGNORECASE)).click()

                if await self._campos_obrigatorios_pendentes(page):
                    self.last_error = "Campos obrigatórios ainda não preenchidos; cotação não enviada corretamente"
                    logger.error(f"[{self.nome}] {self.last_error}")
                    return None

                # Aguarda resposta da API (máx 15s)
                for _ in range(30):
                    if quotation_responses:
                        break
                    await page.wait_for_timeout(500)

                if quotation_responses:
                    response_data = quotation_responses[-1]
                    logger.info(f"[{self.nome}] Resposta API bruta: {response_data}")

                    if response_data.get("errors"):
                        self.last_error = f"Erros de validação BAUER: {response_data.get('errors')}"
                        logger.error(f"[{self.nome}] {self.last_error}")
                        return None

                    if response_data.get("code") == 0 and isinstance(response_data.get("quotation"), dict):
                        quotation = response_data["quotation"]
                        logger.info(f"[{self.nome}] Quotation JSON: {quotation}")
                        valor_frete = float(quotation.get("value", 0))
                        previsao = str(quotation.get("date", "")).strip()
                        numero = str(quotation.get("number", "")).strip()
                        estimate = quotation.get("estimate")

                        # Usar campo 'estimate' se disponível, senão calcular pela data
                        prazo_dias = 0
                        if estimate is not None:
                            try:
                                prazo_dias = int(estimate)
                            except (ValueError, TypeError):
                                pass
                        if prazo_dias == 0 and previsao:
                            try:
                                data_entrega = datetime.strptime(previsao, "%d/%m/%Y")
                                prazo_dias = max(0, (data_entrega - datetime.now()).days)
                            except ValueError:
                                pass

                        restricoes = "Cotação web BAUER"
                        if numero:
                            restricoes += f" - #{numero}"
                        if previsao:
                            restricoes += f" - Previsão: {previsao}"

                        self.last_error = None
                        logger.info(f"[{self.nome}] Cotação obtida via API: R$ {valor_frete:.2f}")
                        return Cotacao(
                            transportadora=self.nome,
                            prazo_dias=prazo_dias,
                            valor_frete=round(valor_frete, 2),
                            restricoes=restricoes,
                            timestamp=datetime.now(),
                        )

                    mensagem = response_data.get("message", "Sem mensagem de erro")
                    self.last_error = f"Cotação recusada pela BAUER: {mensagem}"
                    logger.error(f"[{self.nome}] {self.last_error}")
                    return None

                for _ in range(3):
                    resultado = await self._extrair_resultado(page)
                    if resultado:
                        valor_frete, prazo_dias = resultado
                        logger.info(f"[{self.nome}] Cotação obtida: R$ {valor_frete:.2f} - {prazo_dias} dias")
                        return Cotacao(
                            transportadora=self.nome,
                            prazo_dias=prazo_dias,
                            valor_frete=round(valor_frete, 2),
                            restricoes="Cotação web BAUER",
                            timestamp=datetime.now(),
                        )
                    await page.wait_for_timeout(500)

                if await self._captcha_pendente(page):
                    self.last_error = "Cotação bloqueada por reCAPTCHA pendente"
                    logger.error(f"[{self.nome}] {self.last_error}")

                self.last_error = "Resultado não encontrado na página da BAUER"
                logger.error(f"[{self.nome}] {self.last_error}")
                return None
            finally:
                await context.close()
                await browser.close()

    async def coteir(
        self,
        origem: str,
        destino: str,
        peso: float,
        valor: float,
        cubagens: Optional[list[dict]] = None,
        tipo_frete: str = "cif",
    ) -> Optional[Cotacao]:
        try:
            if cubagens is not None:
                self.cubagens = self._normalizar_cubagens_m(cubagens)
                if cubagens and not self.cubagens:
                    self.last_error = (
                        "Cubagem inválida: nenhuma linha válida para BAUER"
                    )
                    logger.error(f"[{self.nome}] {self.last_error}")
                    return None
                if self.cubagens:
                    primeira = self.cubagens[0]
                    self.quantidade = int(primeira["quantidade"])
                    self.altura_m = float(primeira["altura_m"])
                    self.largura_m = float(primeira["largura_m"])
                    self.profundidade_m = float(primeira["profundidade_m"])
            else:
                self.cubagens = []

            if not self.cubagens and (
                int(self.quantidade or 0) <= 0
                or float(self.altura_m or 0) <= 0
                or float(self.largura_m or 0) <= 0
                or float(self.profundidade_m or 0) <= 0
            ):
                self.last_error = (
                    "Cubagem inválida: BAUER requer quantidade e dimensões reais do romaneio"
                )
                logger.error(f"[{self.nome}] {self.last_error}")
                return None
            return await self._cotar_async(origem=origem, destino=destino, peso=peso, valor=valor, tipo_frete=tipo_frete)
        except Exception as e:
            self.last_error = f"Erro na cotação: {e}"
            logger.error(f"[{self.nome}] {self.last_error}")
            return None
