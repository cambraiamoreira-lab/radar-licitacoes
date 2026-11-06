import streamlit as st
import requests
from datetime import date, timedelta

# Configuração da página
st.set_page_config(page_title="Radar de Licitações", page_icon="🔎", layout="centered")

st.title("🔎 Radar de Licitações")
st.markdown("Encontre oportunidades de compras públicas de forma simples.")

# Campos de filtro
palavra_chave = st.text_input("O que você deseja encontrar?", placeholder="Ex.: material de escritório, uniforme, informática...")

estados = ["Todos", "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO"]
uf = st.selectbox("Estado", estados)

if st.button("Pesquisar Licitações", use_container_width=True):
    with st.spinner("Buscando oportunidades no PNCP..."):
        
        # Datas
        hoje = date.today()
        futuro = hoje + timedelta(days=30)
        data_final = futuro.strftime("%Y%m%d")
        
        url = "https://pncp.gov.br/api/consulta/v1/contratacoes/proposta"
        headers = {"User-Agent": "RadarLicitacoes/1.0 (Python; Streamlit)"}
        
        parametros = {
            "dataFinal": data_final,
            "pagina": 1,
            "tamanhoPagina": 50, # Buscamos 50 e filtramos aqui no app
        }
        
        if uf != "Todos":
            parametros["uf"] = uf
            
        try:
            resposta = requests.get(url, headers=headers, params=parametros, timeout=60)
            resposta.raise_for_status()
            dados = resposta.json()
            
            if dados and 'data' in dados and len(dados['data']) > 0:
                resultados = dados['data']
                
                # Filtro por palavra-chave (já que a API não faz isso diretamente)
                if palavra_chave:
                    resultados_filtrados = [
                        lic for lic in resultados 
                        if palavra_chave.lower() in lic.get('objetoCompra', '').lower()
                    ]
                else:
                    resultados_filtrados = resultados
                
                if len(resultados_filtrados) == 0:
                    st.warning("Nenhuma licitação encontrada com esses termos. Tente buscar apenas uma palavra.")
                else:
                    st.success(f"Encontramos {len(resultados_filtrados)} oportunidade(s) para você!")
                    
                    # Exibindo os resultados em cards
                    for lic in resultados_filtrados:
                        objeto = lic.get('objetoCompra', 'Objeto não informado')
                        orgao = lic.get('orgaoEntidade', {}).get('razaoSocial', 'Órgão não informado')
                        
                        # Formatando o valor estimado se existir
                        valor = lic.get('valorTotalEstimado')
                        if valor:
                            valor_formatado = f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                        else:
                            valor_formatado = "Não informado"
                            
                        with st.container(border=True):
                            st.subheader(objeto)
                            st.write(f"🏢 **Órgão:** {orgao}")
                            st.write(f"💰 **Valor estimado:** {valor_formatado}")
                            
                            # Link para o edital (se existir)
                            link = lic.get('linkSistemaOrigem') or lic.get('urlPortal')
                            if link:
                                st.markdown(f"[🔗 Abrir edital no sistema original]({link})")
                            else:
                                st.caption("Link do edital não disponível na API.")
            else:
                st.info("O PNCP não retornou licitações para os filtros selecionados nesta data.")
                
        except requests.exceptions.ReadTimeout:
            st.error("O servidor do PNCP demorou muito para responder. Tente novamente.")
        except Exception as e:
            st.error(f"Ocorreu um erro ao buscar os dados: {e}")