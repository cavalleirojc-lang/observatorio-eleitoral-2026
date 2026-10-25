"""Consulta conservadora: só publica campos verificados do TSE; nunca inventa zeros."""
import json, os, urllib.request, urllib.error
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'atual.json'
BASE='https://resultados.tse.jus.br/oficial/ele2026'
# Pleito 3221 é o 2º turno, mas o identificador de eleição presidencial
# deve ser lido do arquivo de configuração oficial antes de usar EA20.
CONFIG='https://resultados.tse.jus.br/oficial/comum/config/ele-c.json'

def fetch(url):
    req=urllib.request.Request(url,headers={'User-Agent':'ObservatorioEleitoral2026/1.0','Accept':'application/json'})
    with urllib.request.urlopen(req,timeout=20) as r:
        return json.load(r)

def main():
    result={'status':'aguardando_validacao','consultado_em_utc':datetime.now(timezone.utc).isoformat(),
            'fonte':'Tribunal Superior Eleitoral','url_configuracao':CONFIG,
            'mensagem':'Ainda não foi possível validar automaticamente o identificador e os campos de resultado oficiais. Nenhum voto foi inferido.',
            'dados':None}
    try:
        config=fetch(CONFIG)
        result['configuracao_acessivel']=True
        result['configuracao_tipo']=type(config).__name__
        # NÃO mapear códigos de eleição sem verificar esquema EA11.
        # A próxima etapa implementará EA11/EA14/EA20 após validação.
        result['mensagem']='Configuração oficial acessível; interpretação EA11/EA14/EA20 ainda pendente de validação.'
    except Exception as e:
        result['configuracao_acessivel']=False
        result['mensagem']='Fonte oficial indisponível ou endereço não validado. Sem resultados exibidos.'
        result['erro_tipo']=type(e).__name__
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(result['mensagem'])

if __name__=='__main__':main()
