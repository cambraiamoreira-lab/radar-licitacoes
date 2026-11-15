import streamlit as st
import requests
import pandas as pd
import sqlite3
import time
import json
from datetime import date, timedelta
from dataclasses import dataclass, asdict
from abc import ABC, abstractmethod
from typing import List, Optional, Dict
from contextlib import closing

# ==========================================
# 1. MODELO DE DADOS NORMALIZADO
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
# 2. CAMADA DE CACHE (SQLITE)
# ==========================================
DB_PATH = "radar_cache.db"

def init_db():
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cache_licitacoes (
                chave_busca TEXT PRIMARY KEY,
                dados_json TEXT,
                timestamp REAL
            )
        """)
        conn.commit()

def obter_cache(chave: str, ttl_segundos: int = 1800) -> Optional[List[dict]]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT dados_json, timestamp FROM cache_licitacoes WHERE chave_busca = ?", (chave,))
        row = cursor.fetchone()
        if row:
            dados_json, timestamp = row
            if time.time() - timestamp < ttl_segundos:
                return json.loads(dados_json)
    return None

def salvar_cache(chave: str, dados: List[dict]):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO cache_licitacoes (chave_busca, dados_json, timestamp) VALUES (?, ?, ?)",
            (chave, json.dumps(dados, ensure_ascii=False), time.time())
        )
        conn.commit()

# ==========================================
# 3. INTERFACE DE FONTE E IMPLEMENTAÇÕES
# ==========================================
class FonteLicitacao(ABC):
    @abstractmethod
    def buscar(self, filtros: Dict) -> List[Licitacao]:
        pass

class FontePNCP(FonteLicitacao):
    def __init__(self):
        self.base_url = "https://pncp.gov.br/api/consulta/v1/contratacoes/proposta"

    def _request_com_backoff(self, params: Dict, tentativas: int = 3) -> Optional[Dict]:
        espera = 1
        for i in range(tentativas):
            try:
                resp = requests.get(self.base_url, params=params, timeout=60, headers={"User-Agent": "RadarLicitacoes/2.0"})
                if resp.status_code == 429:
                    time.sleep(espera)
                    espera *= 2  # Backoff exponencial: 1s, 2s, 4s...
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.RequestException:
                if i == tentativas - 1:
                    return None
                time.sleep(1)
        return None

    def buscar(self, filtros: Dict) -> List[Licitacao]:
        resultados = []
        pagina = 1
        palavra_chave = filtros.get("palavra_chave", "").lower()
        uf = filtros.get("uf")
        data_final = filtros.get("data_final", (date.today() + timedelta(days=30)).strftime("%Y%m%d"))

        while True:
            params = {
                "dataFinal": data_final,
                "pagina": pagina,
                "tamanhoPagina": 20  # Mínimo 10 exigido pela API
            }
            if uf and uf != "Todos":
                params["uf"] = uf

            dados = self._request_com_backoff(params)
            if not dados or not dados.get("data"):
                break

            for item in dados["data"]:
                objeto = item.get("objetoCompra", "")
                # Filtro local por palavra-chave (a API do PNCP não possui esse parâmetro)
                if palavra_chave and palavra_chave not in objeto.lower():
                    continue

                resultados.append(Licitacao(
                    fonte="PNCP",
                    id_externo=item.get("numeroControlePNCP", str(item.get("id", ""))),
                    orgao=item.get("orgaoEntidade", {}).get("razaoSocial", "Órgão não informado"),
                    uf=item.get("unidadeOrgao", {}).get("ufSigla", uf or "BR"),
                    municipio=item.get("unidadeOrgao", {}).get("municipioNome"),
                    objeto=objeto,
                    modalidade=item.get("modalidadeNome"),
                    valor_estimado=item.get("valorTotalEstimado"),
                    data_abertura=item.get("dataAberturaProposta"),
                    link_edital=f"https://pncp.gov.br/app/editais/{item.get('orgaoEntidade', {}).get('cnpj', '')}/{item.get('anoCompra', '')}/{item.get('sequencialCompra', '')}",
                    situacao=item.get("situacaoCompraNome")
                ))

            # Se retornar menos que o tamanho da página, não há mais páginas
            if len(dados["data"]) < params["tamanhoPagina"]:
                break

            pagina += 1
            time.sleep(1)  # Delay de 1s entre páginas para não estourar o rate limit

        return resultados


class FonteTransparencia(FonteLicitacao):
    def __init__(self):
        self.base_url = "https://api.portaldatransparencia.gov.br/api-de-dados/licitacoes"
        # A chave gratuita deve ser obtida em: https://portaldatransparencia.gov.br/api-de-dados/cadastrar-email
        self.api_key = st.secrets.get("TRANSPARENCIA_API_KEY", "")

    def buscar(self, filtros: Dict) -> List[Licitacao]:
        if not self.api_key:
            st.warning("⚠️ Chave da API do Portal da Transparência não configurada. Pulando fonte federal.")
            return []

        headers = {"chave-api-dados": self.api_key}
        # O Portal exige filtro por órgão ou data. Usaremos a data como base.
        data_inicio = date.today().strftime("%d/%m/%Y")
        data_fim = (date.today() + timedelta(days=30)).strftime("%d/%m/%Y")
        params = {"dataInicial": data_inicio, "dataFinal": data_fim, "pagina": 1}

        try:
            resp = requests.get(self.base_url, headers=headers, params=params, timeout=60)
            resp.raise_for_status()
            dados = resp.json()
        except Exception:
            return []

        resultados = []
        palavra_chave = filtros.get("palavra_chave", "").lower()

        for item in dados:
            objeto = item.get("objeto", "")
            if palavra_chave and palavra_chave not in objeto.lower():
                continue

            resultados.append(Licitacao(
                fonte="Transparência",
                id_externo=str(item.get("id", "")),
                orgao=item.get("orgao", {}).get("nome", "Órgão não informado"),
                uf=item.get("uf", "BR"),
                municipio=item.get("municipio"),
                objeto=objeto,
                modalidade=item.get("modalidade"),
                valor_estimado=item.get("valor"),
                data_abertura=item.get("dataAbertura"),
                link_edital=None,
                situacao=item.get("situacao")
            ))
        return resultados


class FonteComprasGov(FonteLicitacao):
    def __init__(self):
        self.base_url = "https://compras.dados.gov.br/v1/licitacoes"

    def buscar(self, filtros: Dict) -> List[Licitacao]:
        # A API de dados abertos do Compras.gov.br suporta filtros e paginação.
        params = {"pagina": 1, "tamanhoPagina": 20}
        palavra_chave = filtros.get("palavra_chave")
        if palavra_chave:
            params["objeto"] = palavra_chave  # Busca textual no objeto

        try:
            resp = requests.get(self.base_url, params=params, timeout=60)
            resp.raise_for_status()
            dados = resp.json()
        except Exception:
            return []

        resultados = []
        # A estrutura de resposta pode variar; assume-se uma lista ou chave "_embedded"
        itens = dados if isinstance(dados, list) else dados.get("_embedded", {}).get("licitacoes", [])

        for item in itens:
            resultados.append(Licitacao(
                fonte="Compras.gov.br",
                id_externo=str(item.get("id", "")),
                orgao=item.get("orgao", {}).get("nome", "Órgão não informado"),
                uf=item.get("uf", "BR"),
                municipio=item.get("municipio"),
                objeto=item.get("objeto", ""),
                modalidade=item.get("modalidade"),
                valor_estimado=item.get("valorEstimado"),
                data_abertura=item.get("dataAbertura"),
                link_edital=item.get("link"),
                situacao=item.get("situacao")
            ))
        return resultados

# ==========================================
# 4. ORQUESTRADOR (O CÉREBRO)
# ==========================================
def buscar_licitacoes_completo(filtros: Dict) -> List[dict]:
    # Cria uma chave única para o cache baseada nos filtros
    chave_cache = f"{filtros.get('palavra_chave')}_{filtros.get('uf')}_{filtros.get('esfera')}"
    
    # 1. Tenta buscar no cache primeiro
    cache = obter_cache(chave_cache, ttl_segundos=1800) # Cache de 30 min
    if cache is not None:
        return cache

    # 2. Define quais fontes consultar com base nos filtros
    fontes = [FontePNCP()]  # PNCP é sempre consultado (base nacional)
    
    # Se a esfera for Federal ou Todas, adiciona as fontes federais
    if filtros.get("esfera") in (None, "Todas", "Federal"):
        fontes.append(FonteTransparencia())
        fontes.append(FonteComprasGov())

    # 3. Executa as buscas
    todas_licitacoes = []
    for fonte in fontes:
        try:
            # Passa os filtros para cada fonte
            licitacoes = fonte.buscar(filtros)
            todas_licitacoes.extend([lic.to_dict() for lic in licitacoes])
        except Exception as e:
            st.warning(f"Fonte {fonte.__class__.__name__} indisponível no momento: {e}")

    # 4. Deduplicação (evita a mesma licitação vinda de fontes diferentes)
    vistos = set()
    unicas = []
    for lic in todas_licitacoes:
        chave_dedup = lic["id_externo"]
        if chave_dedup and chave_dedup not in vistos:
            vistos.add(chave_dedup)
            unicas.append(lic)

    # 5. Salva no cache e retorna
    salvar_cache(chave_cache, unicas)
    return unicas

# ==========================================
# 5. INTERFACE STREAMLIT
# ==========================================
st.set_page_config(page_title="Radar de Licitações Multi-Fonte", page_icon="🔎", layout="wide")
st.title("🔎 Radar de Licitações (Multi-Fonte)")

init_db()  # Garante que a tabela de cache existe

# Filtros na barra lateral
with st.sidebar:
    st.header("Filtros")
    palavra_chave = st.text_input("Palavra-chave", placeholder="Ex.: computador, cadeira...")
    estados = ["Todos", "RS", "SP", "RJ", "MG", "PR", "SC", "BA"]
    uf = st.selectbox("Estado", estados)
    esfera = st.selectbox("Esfera", ["Todas", "Federal", "Estadual", "Municipal"])

if st.button("Pesquisar Licitações", use_container_width=True):
    with st.spinner("Consultando múltiplas fontes de dados..."):
        filtros = {
            "palavra_chave": palavra_chave,
            "uf": uf,
            "esfera": esfera
        }
        
        resultados = buscar_licitacoes_completo(filtros)

        if not resultados:
            st.warning("Nenhuma licitação encontrada com os filtros selecionados.")
        else:
            st.success(f"Encontramos **{len(resultados)}** oportunidade(s) após consolidar as fontes!")
            
            # Exibe os resultados em um DataFrame para facilitar a visualização
            df = pd.DataFrame(resultados)
            
            # Renomeia as colunas para português
            df = df.rename(columns={
                "fonte": "Fonte",
                "orgao": "Órgão",
                "uf": "UF",
                "objeto": "Objeto",
                "valor_estimado": "Valor Estimado (R$)",
                "data_abertura": "Abertura",
                "link_edital": "Link"
            })
            
            st.dataframe(
                df[["Fonte", "Órgão", "UF", "Objeto", "Valor Estimado (R$)", "Abertura", "Link"]],
                use_container_width=True,
                hide_index=True
            )