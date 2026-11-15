import streamlit as st
import requests
import pandas as pd
import sqlite3
import time
import json
from datetime import date, timedelta
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict
from contextlib import closing

# ==========================================
# 0. CONFIGURAÇÃO DA PÁGINA E CSS
# ==========================================
st.set_page_config(page_title="Radar de Licitações", page_icon="🔎", layout="centered")

st.markdown("""
<style>
div.stButton > button:first-child {
    background: linear-gradient(90deg, #FF4B4B 0%, #FF7B54 100%);
    color: white;
    border: none;
    border-radius: 10px;
    padding: 12px 24px;
    font-weight: 700;
    font-size: 16px;
    box-shadow: 0 4px 10px rgba(255,75,75,0.3);
    transition: all 0.2s;
}
div.stButton > button:first-child:hover {
    background: linear-gradient(90deg, #FF6B6B 0%, #FF9166 100%);
    color: white;
    transform: translateY(-1px);
    box-shadow: 0 6px 14px rgba(255,75,75,0.4);
}
h1 {
    background: linear-gradient(90deg, #FF4B4B, #FF7B54);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    font-weight: 800;
}
</style>
""", unsafe_allow_html=True)

st.title("🔎 Radar de Licitações")

# ==========================================
# 1. MODELO DE DADOS
# ==========================================
@dataclass
class Licitacao:
    fonte: str
    id_externo: str
    orgao: str
    uf: str
    municipio: Optional[str]
    objeto: str
    modalidade: Optional[str]
    valor_estimado: Optional[float]
    data_abertura: Optional[str]
    link_edital: Optional[str]
    situacao: Optional[str]

    def to_dict(self):
        return asdict(self)

# ==========================================
# 2. CACHE SQLITE
# ==========================================
DB_PATH = "radar_cache.db"

def init_db():
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cache_licitacoes (
                chave_busca TEXT PRIMARY KEY,
                dados_json TEXT,
                timestamp REAL
            )
        """)
        conn.commit()

def obter_cache(chave: str, ttl: int = 900) -> Optional[List[dict]]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        row = conn.execute(
            "SELECT dados_json, timestamp FROM cache_licitacoes WHERE chave_busca=?",
            (chave,)
        ).fetchone()
        if row and (time.time() - row[1]) < ttl:
            return json.loads(row[0])
    return None

def salvar_cache(chave: str, dados: List[dict]):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO cache_licitacoes VALUES (?, ?, ?)",
            (chave, json.dumps(dados, ensure_ascii=False), time.time())
        )
        conn.commit()

# ==========================================
# 3. FONTE PNCP
# ==========================================
class FontePNCP:
    URL = "https://pncp.gov.br/api/consulta/v1/contratacoes/proposta"
    MAX_PAGINAS = 5
    TIMEOUT = 20
    DELAY_ENTRE_PAGINAS = 0.3

    def _get(self, params: Dict) -> Optional[Dict]:
        for tentativa in range(3):
            try:
                resp = requests.get(
                    self.URL,
                    params=params,
                    timeout=self.TIMEOUT,
                    headers={"User-Agent": "RadarLicitacoes/3.0"}
                )
                if resp.status_code == 429:
                    time.sleep(2 ** tentativa)
                    continue
                if resp.status_code >= 400:
                    return None
                return resp.json()
            except requests.exceptions.RequestException:
                time.sleep(1)
        return None

    def buscar(self, filtros: Dict, progresso=None) -> List[Licitacao]:
        resultados = []
        palavra = (filtros.get("palavra_chave") or "").lower()
        uf = filtros.get("uf")
        modalidade = filtros.get("modalidade")

        for pagina in range(1, self.MAX_PAGINAS + 1):
            if progresso:
                progresso(pagina, self.MAX_PAGINAS)

            params = {
                "dataInicial": date.today().strftime("%Y%m%d"),
                "dataFinal": (date.today() + timedelta(days=30)).strftime("%Y%m%d"),
                "pagina": pagina,
                "tamanhoPagina": 20,
            }
            if uf and uf != "Todos":
                params["uf"] = uf
            if modalidade:
                params["codigoModalidadeContratacao"] = modalidade

            dados = self._get(params)
            if not dados or not dados.get("data"):
                break

            for item in dados["data"]:
                objeto = item.get("objetoCompra", "") or ""
                if palavra and palavra not in objeto.lower():
                    continue

                unidade = item.get("unidadeOrgao", {}) or {}
                orgao = item.get("orgaoEntidade", {}) or {}
                cnpj = orgao.get("cnpj", "")
                ano = item.get("anoCompra", "")
                seq = item.get("sequencialCompra", "")

                resultados.append(Licitacao(
                    fonte="PNCP",
                    id_externo=item.get("numeroControlePNCP", str(item.get("id", ""))),
                    orgao=orgao.get("razaoSocial", "Órgão não informado"),
                    uf=unidade.get("ufSigla", uf or "BR"),
                    municipio=unidade.get("municipioNome"),
                    objeto=objeto,
                    modalidade=item.get("modalidadeNome"),
                    valor_estimado=item.get("valorTotalEstimado"),
                    data_abertura=item.get("dataAberturaProposta"),
                    link_edital=f"https://pncp.gov.br/app/editais/{cnpj}/{ano}/{seq}" if cnpj else None,
                    situacao=item.get("situacaoCompraNome")
                ))

            if len(dados["data"]) < params["tamanhoPagina"]:
                break

            time.sleep(self.DELAY_ENTRE_PAGINAS)

        return resultados

# ==========================================
# 4. ORQUESTRADOR
# ==========================================
def buscar_licitacoes(filtros: Dict, usar_fontes_extras: bool, progresso=None) -> List[dict]:
    chave = f"{filtros.get('palavra_chave')}|{filtros.get('uf')}|{filtros.get('modalidade')}|{usar_fontes_extras}"
    cache = obter_cache(chave, ttl=900)
    if cache is not None:
        return cache

    todas = []
    try:
        todas.extend([l.to_dict() for l in FontePNCP().buscar(filtros, progresso)])
    except Exception as e:
        st.warning(f"PNCP indisponível: {e}")

    vistos, unicas = set(), []
    for lic in todas:
        if lic["id_externo"] and lic["id_externo"] not in vistos:
            vistos.add(lic["id_externo"])
            unicas.append(lic)

    salvar_cache(chave, unicas)
    return unicas

# ==========================================
# 5. INTERFACE STREAMLIT
# ==========================================
init_db()

palavra_chave = st.text_input(
    "O que você deseja encontrar?",
    placeholder="Ex.: computador, cadeira, papel..."
)

col1, col2 = st.columns(2)
with col1:
    estados = ["Todos", "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO",
               "MA", "MT", "MS", "MG", "PA", "PB", "PR", "PE", "PI", "RJ",
               "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO"]
    uf = st.selectbox("Estado", estados)
with col2:
    tipos = {
        "Todas": None,
        "Pregão Eletrônico": 5,
        "Concorrência Eletrônica": 3,
        "Dispensa Eletrônica": 13,
        "Leilão Eletrônico": 1,
    }
    tipo_label = st.selectbox("Tipo de Licitação", list(tipos.keys()))

with st.expander("⚙️ Fontes adicionais (opcional)"):
    usar_extras = st.checkbox("Consultar também Portal da Transparência e Compras.gov.br", value=False)
    st.caption("Requer chave de API configurada. Sem isso, o app usa apenas o PNCP.")

if st.button("🔍 Pesquisar Licitações", use_container_width=True):
    with st.spinner("Consultando o PNCP... (isso pode levar alguns segundos)"):
        barra = st.progress(0.0)

        def atualizar(pagina, total):
            barra.progress(pagina / total)

        filtros = {
            "palavra_chave": palavra_chave,
            "uf": uf,
            "modalidade": tipos[tipo_label],
        }
        try:
            resultados = buscar_licitacoes(filtros, usar_extras, progresso=atualizar)
            barra.progress(1.0)
        except Exception as e:
            st.error(f"Erro na busca: {e}")
            resultados = []

    if not resultados:
        st.warning("Nenhuma licitação encontrada. Tente uma palavra-chave mais simples ou amplie o estado para 'Todos'.")
    else:
        st.success(f"Encontramos **{len(resultados)}** oportunidade(s)!")
        for lic in resultados[:100]:
            with st.container(border=True):
                st.subheader(lic["objeto"] or "Objeto não informado")
                cols = st.columns(3)
                cols[0].write(f"🏢 **Órgão:** {lic['orgao']}")
                cols[1].write(f"📍 **UF:** {lic['uf']}")
                cols[2].write(f"🏷️ **Modalidade:** {lic['modalidade'] or '—'}")
                if lic.get("valor_estimado"):
                    st.write(f"💰 **Valor estimado:** R$ {lic['valor_estimado']:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
                if lic.get("data_abertura"):
                    st.write(f"📅 **Abertura:** {lic['data_abertura']}")
                if lic.get("link_edital"):
                    st.markdown(f"[🔗 Abrir edital no PNCP]({lic['link_edital']})", unsafe_allow_html=True)