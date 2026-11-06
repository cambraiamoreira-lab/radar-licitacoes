import streamlit as st
import requests

st.set_page_config(page_title="Radar de Licitações", page_icon="🔎", layout="centered")
st.title("🔎 Radar de Licitações")

palavra_chave = st.text_input("O que você deseja encontrar?", placeholder="Ex.: computador, cadeira...")
estados = ["Todos", "RS", "SP", "RJ", "MG", "PR", "SC", "BA"]
uf = st.selectbox("Estado", estados)

if st.button("Pesquisar Licitações", use_container_width=True):
    with st.spinner("Buscando oportunidades no PNCP..."):
        
        # FORÇANDO UMA DATA FIXA PARA TESTE (Final de 2024)
        data_final = "20241231" 
        
        url = "https://pncp.gov.br/api/consulta/v1/contratacoes/proposta"
        headers = {"User-Agent": "RadarLicitacoes/1.0"}
        
        parametros = {
            "dataFinal": data_final,
            "pagina": 1,
            "tamanhoPagina": 50,
        }
        if uf != "Todos":
            parametros["uf"] = uf
            
        try:
            resposta = requests.get(url, headers=headers, params=parametros, timeout=60)
            resposta.raise_for_status()
            dados = resposta.json()
            
            # RAIO-X
            total_bruto = len(dados.get('data', []))
            st.info(f"🔍 O PNCP retornou **{total_bruto}** licitações brutas para os filtros de data e estado.")
            
            if total_bruto > 0:
                resultados = dados['data']
                if palavra_chave:
                    resultados_filtrados = [lic for lic in resultados if palavra_chave.lower() in lic.get('objetoCompra', '').lower()]
                    st.info(f"🔍 Após filtrar por '{palavra_chave}', sobraram **{len(resultados_filtrados)}**.")
                else:
                    resultados_filtrados = resultados
                
                if len(resultados_filtrados) == 0:
                    st.warning("Nenhuma licitação encontrada com essa palavra-chave. Tente buscar apenas uma palavra (ex: 'papel').")
                else:
                    st.success(f"Encontramos {len(resultados_filtrados)} oportunidade(s)!")
                    for lic in resultados_filtrados:
                        objeto = lic.get('objetoCompra', 'Objeto não informado')
                        orgao = lic.get('orgaoEntidade', {}).get('razaoSocial', 'Órgão não informado')
                        with st.container(border=True):
                            st.subheader(objeto)
                            st.write(f"🏢 **Órgão:** {orgao}")
            else:
                st.warning("A API do PNCP não retornou nenhuma licitação para os filtros selecionados.")
                
        except Exception as e:
            st.error(f"Erro: {e}")