"""Provider COOPEX - Sistema SSW de Transportes (mesmo portal da Eucatur)."""
from datetime import datetime
from typing import Optional
import re
from playwright.async_api import async_playwright, Page
from fretebot.providers.base import ProviderBase
from fretebot.models import Cotacao
from fretebot.logging_conf import get_logger

logger = get_logger(__name__)


class CoopexProvider(ProviderBase):
    """Provider COOPEX via portal SSW (sistema.ssw.inf.br)."""

    LOGIN_URL = "https://sistema.ssw.inf.br/bin/ssw0422"
    COTACAO_URL = "https://sistema.ssw.inf.br/bin/ssw1608"

    def __init__(
        self,
        dominio: str,
        usuario: str,
        senha: str,
        usar_cache: bool = True,
        headless: bool = True,
    ):
        super().__init__(nome="COOPEX")
        self.dominio = dominio
        self.usuario = usuario
        self.senha = senha
        self.headless = headless
        self.last_error: str | None = None
        self._browser = None
        self._context = None
        self._page = None
        self._playwright = None
        self._logged_in = False

    async def _init_browser(self):
        """Inicializa browser Playwright."""
        if self._browser:
            if self._browser.is_connected():
                return
            logger.warning(f"[{self.nome}] Browser desconectado, reinicializando...")
            await self.cleanup()

        from fretebot.providers.base import launch_browser_resilient
        self._browser = await launch_browser_resilient(
            headless=self.headless,
            args=['--disable-blink-features=AutomationControlled', '--no-sandbox'],
        )
        self._context = await self._browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        )
        self._page = await self._context.new_page()

    async def _login(self):
        """Faz login no portal SSW."""
        if self._logged_in:
            return
        try:
            logger.info(f"[{self.nome}] Fazendo login no SSW...")
            await self._page.goto(self.LOGIN_URL, wait_until='domcontentloaded', timeout=60000)
            await self._page.wait_for_timeout(800)

            await self._page.locator('input[name=f1]').fill(self.dominio)
            await self._page.locator('input[name=f3]').fill(self.usuario)
            await self._page.locator('input[name=f4]').fill(self.senha)
            await self._page.locator('a:has-text("►")').click()

            # Aguarda redirecionamento pós-login (polling até 10s)
            login_ok = False
            for _wait in range(20):
                await self._page.wait_for_timeout(500)
                url = self._page.url
                if 'ssw0422' not in url or 'menu' in url:
                    login_ok = True
                    break
            if not login_ok:
                raise Exception(f"Login falhou, URL: {self._page.url}")

            logger.info(f"[{self.nome}] Login OK")
            self._logged_in = True
        except Exception as e:
            self.last_error = f"Erro no login: {e}"
            logger.error(f"[{self.nome}] {self.last_error}")
            raise

    async def pre_login(self):
        """Inicializa browser e faz login antecipadamente."""
        await self._init_browser()
        await self._login()

    async def cleanup(self):
        """Fecha o browser."""
        try:
            if self._page and not self._page.is_closed():
                await self._page.close()
        except Exception:
            pass
        try:
            if self._context:
                await self._context.close()
        except Exception:
            pass
        try:
            if self._browser and self._browser.is_connected():
                await self._browser.close()
        except Exception:
            pass
        self._browser = None
        self._context = None
        self._page = None
        self._logged_in = False

    @staticmethod
    def _normalizar_cubagens_cm(cubagens: Optional[list[dict]]) -> list[dict]:
        validas: list[dict] = []
        if not isinstance(cubagens, list):
            return validas
        for row in cubagens:
            if not isinstance(row, dict):
                continue
            try:
                qtd = int(row.get("quantidade", 0) or 0)
                comp = int(row.get("comprimento_cm", 0) or 0)
                larg = int(row.get("largura_cm", 0) or 0)
                alt = int(row.get("altura_cm", 0) or 0)
            except Exception:
                continue
            if qtd <= 0 or comp <= 0 or larg <= 0 or alt <= 0:
                continue
            validas.append(
                {
                    "quantidade": qtd,
                    "comprimento_cm": comp,
                    "largura_cm": larg,
                    "altura_cm": alt,
                }
            )
        return validas

    async def _navegar_cotacao(self):
        """Navega para a tela de cotação (ssw1608)."""
        await self._page.goto(self.COTACAO_URL, wait_until='domcontentloaded', timeout=60000)
        await self._page.wait_for_timeout(800)

        has_form = await self._page.locator('input[name=f2]').count()
        if has_form == 0:
            logger.warning(f"[{self.nome}] Formulário não encontrado, tentando re-login...")
            self._logged_in = False
            await self._login()
            await self._page.goto(self.COTACAO_URL, wait_until='domcontentloaded', timeout=60000)
            await self._page.wait_for_timeout(800)
            has_form = await self._page.locator('input[name=f2]').count()
            if has_form == 0:
                raise Exception("Formulário de cotação não carregou mesmo após re-login")

        logger.info(f"[{self.nome}] Tela de cotação carregada")

    async def _preencher_cotacao(self, origem: str, destino: str, peso: float, valor: float,
                                volumes: int = 1, cubagem_m3: float = 0.0,
                                comprimento_cm: int = 0, largura_cm: int = 0, altura_cm: int = 0,
                                cnpj_remetente: str = "", cnpj_destinatario: str = "",
                                cubagens: Optional[list[dict]] = None,
                                cnpj_pagador: str = "", tipo_frete: str = "1"):
        """Preenche o formulário de cotação SSW via JavaScript."""
        page = self._page
        cnpj_pagador = (cnpj_pagador or "40223106000179").replace('.', '').replace('/', '').replace('-', '').strip()

        # CNPJ pagador + trigger lookup
        await page.evaluate(f'''() => {{
            const f2 = document.querySelector('input[name=f2]');
            f2.value = '{cnpj_pagador}';
            if (typeof pag === 'function') pag('{cnpj_pagador}');
        }}''')
        await page.wait_for_timeout(1000)

        # CEP origem + trigger lookup
        cep_orig = origem.replace('-', '').strip()
        await page.evaluate(f'''() => {{
            const f6 = document.querySelector('input[name=f6]');
            f6.value = '{cep_orig}';
            if (typeof ce2 === 'function') ce2('{cep_orig}');
        }}''')
        await page.wait_for_timeout(1000)

        # CEP destino + trigger lookup
        cep_dest = destino.replace('-', '').strip()
        await page.evaluate(f'''() => {{
            const f8 = document.querySelector('input[name=f8]');
            f8.value = '{cep_dest}';
            if (typeof cep === 'function') cep('{cep_dest}');
        }}''')
        await page.wait_for_timeout(1000)

        # Frete CIF/FOB, Coletar S, Contribuinte S, Entrega difícil N
        await page.evaluate('''(tipoFrete) => {
            const f9 = document.querySelector('input[name=f9]');
            f9.value = tipoFrete;
            if (typeof f_c === 'function') f_c(tipoFrete);
            document.querySelector('input[name=f10]').value = 'S';
            document.querySelector('input[name=f13]').value = 'S';
            document.querySelector('input[name=f14]').value = 'N';
        }''', tipo_frete)

        cnpj_rem = cnpj_remetente.replace('.', '').replace('/', '').replace('-', '').strip()
        cnpj_dest = cnpj_destinatario.replace('.', '').replace('/', '').replace('-', '').strip()
        await page.evaluate(
            """({ mercadoria, cnpjRem, cnpjDest }) => {
                const setInputValue = (name, value) => {
                    const el = document.querySelector(`input[name="${name}"]`);
                    if (!el) return false;
                    el.value = value;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.dispatchEvent(new Event('blur', { bubbles: true }));
                    return true;
                };
                setInputValue('f4', mercadoria);
                if (cnpjRem) setInputValue('cgc_rem', cnpjRem);
                if (cnpjDest) setInputValue('f12', cnpjDest);
            }""",
            {
                "mercadoria": "001",
                "cnpjRem": cnpj_rem,
                "cnpjDest": cnpj_dest,
            },
        )

        # Valor NF (f15) - preenchimento robusto.
        valor_fmt = f"{valor:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
        await page.evaluate(
            """(valorNf) => {
                const el = document.querySelector("input[name='f15']");
                if (!el) return;
                el.value = valorNf;
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                el.dispatchEvent(new Event('blur', { bubbles: true }));
            }""",
            valor_fmt,
        )

        # Quantidade volumes
        await page.evaluate(f'() => {{ document.querySelector("input[name=f16]").value = "{volumes}"; }}')

        # Peso real do romaneio
        peso_fmt = f"{peso:.3f}".replace('.', ',')
        await page.evaluate(f'() => {{ document.querySelector("input[name=f18]").value = "{peso_fmt}"; }}')

        # Quantidade de pares (f17) deve ser ignorada.
        await page.evaluate("""() => {
            const f17 = document.querySelector('input[name=f17]');
            if (!f17) return;
            f17.value = '';
            f17.dispatchEvent(new Event('input', { bubbles: true }));
            f17.dispatchEvent(new Event('change', { bubbles: true }));
            f17.dispatchEvent(new Event('blur', { bubbles: true }));
        }""")

        # Preencher campo de cubagem (m³) explicitamente.
        if cubagem_m3 > 0:
            cubagem_fmt = f"{cubagem_m3:.4f}".replace('.', ',')
            await page.evaluate(f'''() => {{
                const names = ['f20', 'cubagem', 'cubagem_m3', 'f19'];
                let el = null;
                for (const name of names) {{
                    el = document.querySelector('input[name=' + name + ']');
                    if (el) break;
                }}
                if (!el) {{
                    const inputs = Array.from(document.querySelectorAll('input'));
                    for (const input of inputs) {{
                        const rowText =
                            (input.closest('tr')?.innerText || '') + ' ' +
                            (input.parentElement?.innerText || '');
                        if (/cubagem/i.test(rowText)) {{
                            el = input;
                            break;
                        }}
                    }}
                }}
                if (el) {{
                    el.value = '{cubagem_fmt}';
                    el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    el.dispatchEvent(new Event('blur', {{ bubbles: true }}));
                }}
            }}''')

        # Preencher cubagem por volume (campos cuba1..cuba11)
        # Agrupa caixas de mesmo tamanho para economizar campos
        cubagens_validas = self._normalizar_cubagens_cm(cubagens)
        if cubagens_validas:
            # Agrupa por dimensão: {"50x40x30": quantidade_total}
            grupos: dict[str, int] = {}
            for cub in cubagens_validas:
                chave = f'{int(cub["comprimento_cm"])}x{int(cub["largura_cm"])}x{int(cub["altura_cm"])}'
                grupos[chave] = grupos.get(chave, 0) + int(cub["quantidade"])

            # Expande os grupos em valores individuais
            cuba_values: list[str] = []
            for dim, qtd in grupos.items():
                for _ in range(qtd):
                    cuba_values.append(dim)

            if len(cuba_values) <= 11:
                for i, cuba_value in enumerate(cuba_values, start=1):
                    await page.evaluate(f'''() => {{
                        const el = document.querySelector('input[name=cuba{i}]');
                        if (el) el.value = '{cuba_value}';
                    }}''')
                logger.info(f"[{self.nome}] Preencheu {len(cuba_values)} campos cuba individuais")
            else:
                logger.info(f"[{self.nome}] {len(cuba_values)} volumes > 11 campos cuba, usando apenas cubagem m³ total")

        # Reaplica f15 no final, pois alguns scripts do SSW podem limpar/reformatar o campo.
        await page.evaluate(
            """(valorNf) => {
                const el = document.querySelector("input[name='f15']");
                if (!el) return;
                el.value = valorNf;
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                el.dispatchEvent(new Event('blur', { bubbles: true }));
            }""",
            valor_fmt,
        )

        await page.wait_for_timeout(500)

        # Verificar campos preenchidos - scan completo de todos os inputs
        form_check = await page.evaluate('''() => {
            const result = {};
            document.querySelectorAll('input').forEach(el => {
                if (el.name) result[el.name] = el.value || '';
            });
            return result;
        }''')
        logger.info(
            f"[{self.nome}] Formulário: {cep_orig} → {cep_dest}, peso={peso}kg, "
            f"cubagem={cubagem_m3:.4f}m³, {volumes}vol, linhas_cubagem={len(cubagens_validas)}, "
            f"coletar=S, R${valor}"
        )
        logger.info(f"[{self.nome}] Todos os campos SSW: {form_check}")

    async def _submeter_e_extrair(self) -> Optional[Cotacao]:
        """Submete a cotação e extrai o resultado."""
        page = self._page

        # Capturar resposta XML do submit
        xml_responses = []

        async def capture_response(response):
            url = response.url.lower()
            if 'ssw' in url or 'cotac' in url or 'frete' in url:
                try:
                    body = await response.text()
                    xml_responses.append(body)
                except:
                    pass

        handler = lambda r: __import__('asyncio').ensure_future(capture_response(r))
        page.on('response', handler)

        try:
            return await self._submeter_e_extrair_inner(page, xml_responses)
        finally:
            page.remove_listener('response', handler)

    async def _submeter_e_extrair_inner(self, page, xml_responses) -> Optional[Cotacao]:
        # Submit via sim()
        await page.evaluate('() => { if (typeof sim === "function") sim(); }')

        # Polling: aguardar vlr_frete ser populado no DOM (até 25s)
        results = {}
        for _poll in range(25):
            await page.wait_for_timeout(1000)
            results = await page.evaluate('''() => {
                const result = {};
                document.querySelectorAll('input').forEach(el => {
                    if (el.name) result[el.name] = el.value || '';
                });
                return result;
            }''')
            vlr = (results.get('vlr_frete', '') or results.get('vlr_total', '') or
                   results.get('valor_frete', '') or results.get('total_geral', '') or
                   results.get('total_frete', ''))
            if vlr and vlr != '0,00':
                logger.info(f"[{self.nome}] vlr_frete encontrado após {_poll+1}s")
                break
            # Se XML retornou erro, não precisa continuar esperando
            if xml_responses:
                for xml in xml_responses:
                    if '<erro>' in xml and 'ERRO' in xml.upper():
                        logger.info(f"[{self.nome}] Erro XML detectado, parando polling")
                        break
                else:
                    continue
                break

        logger.info(f"[{self.nome}] Todos os campos resultado DOM: {results}")

        # Verificar erros na resposta XML
        erro = None
        erro_msg = None
        for xml in xml_responses:
            erro_match = re.search(r'<erro>([^<]+)</erro>', xml)
            msg_match = re.search(r'<mensagem>(.*?)</mensagem>', xml, re.DOTALL)
            if erro_match and erro_match.group(1) != '':
                erro = erro_match.group(1)
                msg = msg_match.group(1) if msg_match else ''
                msg = msg.replace('&amp;nbsp;', ' ').replace('&amp;', '&')
                msg = re.sub(r'&\w+;', ' ', msg).replace('<br>', ' ').strip()
                msg = re.sub(r'<[^>]+>', '', msg).strip()
                erro_msg = msg
                logger.warning(f"[{self.nome}] Aviso SSW: {erro} - {msg}")

        restricao_risco = None
        if erro and 'ERRO' in erro.upper():
            # "area de risco" e aviso, nao rejeicao
            if 'risco' in (erro_msg or '').lower():
                restricao_risco = erro_msg
                logger.info(f"[{self.nome}] Aviso de risco (nao fatal): {erro_msg}")
            else:
                self.last_error = f"Rota não atendida pelo SSW: {erro_msg}"
                logger.info(f"[{self.nome}] {self.last_error}")
                return None

        vlr_frete_str = (results.get('vlr_frete', '') or results.get('vlr_total', '') or
                        results.get('valor_frete', '') or results.get('total_geral', '') or
                        results.get('total_frete', ''))
        # Fallback DOM: buscar em todo campo com nome contendo vlr/valor/total/frete/coleta
        if not vlr_frete_str or vlr_frete_str == '0,00':
            for name, val in results.items():
                if not val or val == '0,00':
                    continue
                nl = name.lower()
                if any(kw in nl for kw in ('vlr', 'valor', 'total', 'frete', 'coleta')):
                    if re.match(r'[\d.]+,\d{2}$', val):
                        vlr_frete_str = val
                        logger.info(f"[{self.nome}] valor encontrado em campo DOM '{name}': {val}")
                        break
        nro_cotacao = results.get('nro_cotacao', '') or results.get('nr_cotacao', '')
        prazo_str = results.get('prazo', '')

        # Fallback: extrair valor do frete da resposta XML se DOM não retornou
        if (not vlr_frete_str or vlr_frete_str == '0,00') and xml_responses:
            for xml in xml_responses:
                for tag in ('vlr_frete', 'vlr_total', 'valor_frete', 'total_frete', 'vlr_total_frete',
                            'total_geral', 'vlr_total_geral', 'vlr_coleta', 'frt_valor'):
                    m_vlr = re.search(rf'<{tag}>([^<]+)</{tag}>', xml)
                    if m_vlr and m_vlr.group(1).strip() and m_vlr.group(1).strip() != '0,00':
                        vlr_frete_str = m_vlr.group(1).strip()
                        logger.info(f"[{self.nome}] vlr_frete extraído do XML tag <{tag}>: {vlr_frete_str}")
                        break
                if vlr_frete_str and vlr_frete_str != '0,00':
                    break
                if not nro_cotacao:
                    m_nro = re.search(r'<nro_cotacao>([^<]+)</nro_cotacao>', xml)
                    if m_nro:
                        nro_cotacao = m_nro.group(1).strip()
                if not prazo_str:
                    m_prazo = re.search(r'<prazo>([^<]+)</prazo>', xml)
                    if m_prazo:
                        prazo_str = m_prazo.group(1).strip()

        # Fallback 2: buscar valor monetário em qualquer campo visível da página
        if not vlr_frete_str or vlr_frete_str == '0,00':
            try:
                page_data = await page.evaluate('''() => {
                    const result = {};
                    const els = document.querySelectorAll('td, span, div, label, b, strong');
                    for (const el of els) {
                        const txt = (el.innerText || '').trim();
                        if (!txt) continue;
                        const parent = el.closest('tr, div, section');
                        const ctx = (parent ? parent.innerText : '').toLowerCase();
                        if (ctx.includes('frete') || ctx.includes('total')) {
                            const m = txt.match(/^[\\d.,]+$/);
                            if (m && txt.length >= 3 && txt.includes(',')) {
                                result['frete_text'] = txt;
                                break;
                            }
                        }
                    }
                    return result;
                }''')
                frete_text = page_data.get('frete_text', '')
                if frete_text and frete_text != '0,00':
                    vlr_frete_str = frete_text
                    logger.info(f"[{self.nome}] vlr_frete extraído de texto visível: {vlr_frete_str}")
            except Exception as e:
                logger.debug(f"[{self.nome}] Fallback texto visível falhou: {e}")

        # Fallback 3: buscar valor monetário nos XMLs brutos com regex mais amplo
        if (not vlr_frete_str or vlr_frete_str == '0,00') and xml_responses:
            for xml in xml_responses:
                m_gen = re.search(r'frete[^>]*>(\d[\d.,]+)<', xml, re.IGNORECASE)
                if m_gen:
                    candidate = m_gen.group(1).strip()
                    if candidate and candidate != '0,00':
                        vlr_frete_str = candidate
                        logger.info(f"[{self.nome}] vlr_frete extraído via regex genérico XML: {vlr_frete_str}")
                        break

        # Fallback 4: buscar qualquer valor monetário nos XMLs (sem contexto 'frete')
        if (not vlr_frete_str or vlr_frete_str == '0,00') and xml_responses:
            for xml in xml_responses:
                for m_tag in re.finditer(r'<(\w+)>([\d.,]+)</\1>', xml):
                    tag_name = m_tag.group(1).lower()
                    tag_val = m_tag.group(2).strip()
                    if any(skip in tag_name for skip in ('erro', 'prazo', 'cep', 'cnpj', 'cpf', 'ie', 'nro', 'qtd', 'peso', 'cubagem', 'volume', 'ddd', 'fone')):
                        continue
                    if ',' in tag_val and tag_val != '0,00' and len(tag_val) >= 4:
                        vlr_frete_str = tag_val
                        logger.info(f"[{self.nome}] vlr_frete extraído de XML tag <{tag_name}>: {vlr_frete_str}")
                        break
                if vlr_frete_str and vlr_frete_str != '0,00':
                    break

        # Fallback 5: extrair todo texto visível da página e procurar valor monetário
        if not vlr_frete_str or vlr_frete_str == '0,00':
            try:
                page_text = await page.evaluate('() => document.body?.innerText || ""')
                if page_text:
                    for m_val in re.finditer(r'R?\$?\s*([\d.]+,\d{2})', page_text):
                        val_candidate = m_val.group(1).strip()
                        if val_candidate and val_candidate != '0,00':
                            start = max(0, m_val.start() - 100)
                            end = min(len(page_text), m_val.end() + 100)
                            context = page_text[start:end].lower()
                            if any(kw in context for kw in ('frete', 'total', 'valor', 'coleta', 'cotação', 'cotacao')):
                                vlr_frete_str = val_candidate
                                logger.info(f"[{self.nome}] vlr_frete extraído do texto da página: {vlr_frete_str}")
                                break
            except Exception as e:
                logger.debug(f"[{self.nome}] Fallback texto completo falhou: {e}")

        if not vlr_frete_str or vlr_frete_str == '0,00':
            # Log de diagnóstico: XMLs capturados para análise futura
            if xml_responses:
                for idx, xml in enumerate(xml_responses):
                    logger.warning(f"[{self.nome}] XML #{idx+1} capturado ({len(xml)} chars): {xml[:500]}")
            self.last_error = f"Sem valor de frete retornado (campos DOM: vlr_frete={vlr_frete_str!r}, nro_cotacao={nro_cotacao!r}, prazo={prazo_str!r})"
            logger.warning(f"[{self.nome}] {self.last_error}")
            return None

        try:
            valor_frete = float(vlr_frete_str.replace('.', '').replace(',', '.'))
        except ValueError:
            self.last_error = f"Erro ao parsear valor: {vlr_frete_str}"
            logger.error(f"[{self.nome}] {self.last_error}")
            return None

        prazo_dias = 0
        if prazo_str:
            try:
                data_entrega = datetime.strptime(prazo_str, "%d/%m/%y")
                prazo_dias = (data_entrega - datetime.now()).days
                if prazo_dias < 0:
                    prazo_dias = 0
            except ValueError:
                logger.warning(f"[{self.nome}] Prazo não parseável: {prazo_str}")

        cotacao = Cotacao(
            transportadora=self.nome,
            prazo_dias=prazo_dias,
            valor_frete=valor_frete,
            restricoes=restricao_risco,
        )
        logger.info(f"[{self.nome}] Cotação #{nro_cotacao}: R$ {valor_frete:.2f}, {prazo_dias} dias")
        return cotacao

    async def coteir(self, origem: str, destino: str, peso: float, valor: float,
                    volumes: int = 1, cubagem_m3: float = 0.0,
                    comprimento_cm: int = 0, largura_cm: int = 0, altura_cm: int = 0,
                    cnpj_remetente: str = "", cnpj_destinatario: str = "",
                    cubagens: Optional[list[dict]] = None,
                    cnpj_pagador: str = "", tipo_frete: str = "1") -> Optional[Cotacao]:
        """Realiza cotação de frete via portal SSW COOPEX."""
        self.last_error = None
        try:
            cubagens_cm = self._normalizar_cubagens_cm(cubagens)
            if cubagens_cm:
                soma = sum(int(c["quantidade"]) for c in cubagens_cm)
                volumes = soma
                # Calcula m³ total a partir das cubagens individuais
                if cubagem_m3 <= 0:
                    cubagem_m3 = sum(
                        (c["comprimento_cm"] * c["largura_cm"] * c["altura_cm"] / 1_000_000.0) * c["quantidade"]
                        for c in cubagens_cm
                    )
            elif volumes > 0 and comprimento_cm > 0 and largura_cm > 0 and altura_cm > 0:
                cubagens_cm = [
                    {
                        "quantidade": int(volumes),
                        "comprimento_cm": int(comprimento_cm),
                        "largura_cm": int(largura_cm),
                        "altura_cm": int(altura_cm),
                    }
                ]
                if cubagem_m3 <= 0:
                    cubagem_m3 = (comprimento_cm * largura_cm * altura_cm / 1_000_000.0) * volumes
            elif cubagem_m3 > 0:
                # Sem cubagens individuais, mas tem m³ total — prossegue
                cubagens_cm = []
            else:
                self.last_error = (
                    f"Cotação bloqueada: cubagens reais ausentes/inválidas "
                    f"(volumes={volumes}, dims_cm={comprimento_cm}x{largura_cm}x{altura_cm}, m3={cubagem_m3})"
                )
                logger.error(f"[{self.nome}] {self.last_error}")
                return None

            await self._init_browser()
            await self._login()

            # Criar nova página para cada cotação (mantém sessão via cookies do contexto)
            if self._logged_in:
                try:
                    await self._page.close()
                except Exception:
                    pass
                self._page = await self._context.new_page()

            await self._navegar_cotacao()
            await self._preencher_cotacao(origem, destino, peso, valor, volumes, cubagem_m3,
                                         comprimento_cm, largura_cm, altura_cm,
                                         cnpj_remetente, cnpj_destinatario,
                                         cubagens_cm, cnpj_pagador, tipo_frete)
            return await self._submeter_e_extrair()
        except Exception as e:
            self.last_error = f"Erro na cotação: {e}"
            logger.error(f"[{self.nome}] {self.last_error}")
            return None
