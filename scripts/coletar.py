"""Consulta EA11/EA20 presidencial 2026, com validações conservadoras."""
import json, urllib.request, urllib.error
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data' / 'atual.json'
CONFIG = 'https://resultados.tse.jus.br/oficial/comum/config/ele-c.json'
BASE = 'https://resultados.tse.jus.br/oficial'

def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent':'ObservatorioEleitoral2026/2.0', 'Accept':'application/json'})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.load(resp)

def inteiro(valor):
    if isinstance(valor, bool): raise ValueError('booleano não é contagem')
    if isinstance(valor, int): return valor
    if isinstance(valor, str) and valor.isdigit(): return int(valor)
    raise ValueError(f'Contagem inválida: {valor!r}')

def main():
    agora = datetime.now(timezone.utc).isoformat()
    saida = {'status':'indisponivel','consultado_em_utc':agora,'fonte':'TSE - EA11/EA20',
             'configuracao_acessivel':False,'dados':None,
             'mensagem':'Aguardando resposta e validação do TSE.'}
    try:
        cfg = fetch(CONFIG)
        saida['configuracao_acessivel'] = True
        pleitos = [p for p in cfg.get('pl',[]) if str(p.get('cd'))=='3221' and p.get('c')=='ele2026']
        if len(pleitos)!=1: raise ValueError('Pleito 3221 não identificado univocamente no EA11')
        eleicoes = [e for e in pleitos[0].get('e',[]) if str(e.get('cd'))=='6258' and str(e.get('t'))=='2'
                    and any(a.get('cd')=='br' and any(str(c.get('cd'))=='1' for c in a.get('cp',[])) for a in e.get('abr',[]))]
        if len(eleicoes)!=1: raise ValueError('Eleição presidencial 6258 não confirmada no EA11')
        url = f"{BASE}/{pleitos[0]['c']}/6258/dados/br/br-c0001-e006258-u.json"
        doc = fetch(url)
        saida['url_resultado'] = url
        if str(doc.get('ele'))!='6258' or str(doc.get('t'))!='2' or doc.get('cdabr')!='br':
            raise ValueError('EA20 retornou eleição, turno ou abrangência incompatíveis')
        cargos = [c for c in doc.get('carg',[]) if str(c.get('cd'))=='1']
        if len(cargos)!=1: raise ValueError('Cargo de presidente ausente ou duplicado')
        candidatos=[]
        for agr in cargos[0].get('agr',[]):
            for par in agr.get('par',[]):
                for cand in par.get('cand',[]):
                    candidatos.append({'numero':str(cand['n']), 'nome':cand.get('nmu') or cand.get('nm'),
                                       'votos':inteiro(cand['vap'])})
        if len(candidatos)!=2 or len(set(c['numero'] for c in candidatos))!=2:
            raise ValueError('Não foram encontrados exatamente dois candidatos presidenciais')
        s=doc.get('s',{}); v=doc.get('v',{}); e=doc.get('e',{})
        total_secoes=inteiro(s['ts']); secoes=inteiro(s['st']); validos=inteiro(v['vv'])
        if not (0<=secoes<=total_secoes) or sum(c['votos'] for c in candidatos)!=validos:
            raise ValueError('Contagens inconsistentes no EA20; resultados não publicados')
        if validos<0: raise ValueError('Votos válidos negativos')
        candidatos.sort(key=lambda c:c['votos'], reverse=True)
        for c in candidatos: c['percentual']=round(c['votos']*100/validos,2) if validos else None
        dados={'eleicao':'6258','turno':2,'secoes_totalizadas':secoes,'secoes_total':total_secoes,
               'votos_validos':validos,'candidatos':candidatos,
               'diferenca_votos':abs(candidatos[0]['votos']-candidatos[1]['votos']),
               'eleitorado_apto':inteiro(e['te']),
               'comparecimento':inteiro(e['c']), 'abstencoes':inteiro(e['a']),
               'brancos':inteiro(v['vb']), 'nulos':inteiro(v['vn']),
               'gerado_em_tse':f"{doc.get('dg','')} {doc.get('hg','')}", 'idg':str(doc.get('idg',''))}
        saida['dados']=dados
        saida['status']='sem_totalizacao' if secoes==0 else 'parcial_validada'
        saida['mensagem']='Arquivo oficial validado. Totalização ainda não iniciada.' if secoes==0 else 'Resultado nacional oficial disponível (EA20).'
        print(saida['mensagem'], '| seções:', secoes, '| votos válidos:', validos)
    except Exception as exc:
        saida['status']='indisponivel'
        saida['mensagem']='Não foi possível validar o resultado oficial. Dados ocultados por segurança.'
        saida['erro_tipo']=type(exc).__name__
        print('Consulta sem validação:', type(exc).__name__, str(exc)[:240])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(saida,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

if __name__=='__main__': main()
