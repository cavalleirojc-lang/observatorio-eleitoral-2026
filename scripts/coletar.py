"""Coleta conservadora EA11/EA20 do TSE, com cache-busting, histórico e diagnóstico."""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
OUT = DATA / 'atual.json'
HIST = DATA / 'historico.json'
CONFIG = 'https://resultados.tse.jus.br/oficial/comum/config/ele-c.json'
BASE = 'https://resultados.tse.jus.br/oficial'
UFS = 'ac al ap am ba ce df es go ma mt ms mg pa pb pr pe pi rj rn rs ro rr sc sp se to'.split()
REGIOES = {'Norte':'ac ap am pa ro rr to','Nordeste':'al ba ce ma pb pe pi rn se','Centro-Oeste':'df go mt ms','Sudeste':'es mg rj sp','Sul':'pr rs sc'}

def fetch(url):
    # Query única evita reaproveitar respostas antigas em caches intermediários.
    sep = '&' if '?' in url else '?'
    fresh_url = url + sep + urllib.parse.urlencode({'_ts': time.time_ns()})
    req = urllib.request.Request(fresh_url, headers={
        'User-Agent': 'Mozilla/5.0 (compatible; ObservatorioEleitoral2026/3.0)',
        'Accept': 'application/json',
        'Cache-Control': 'no-cache, no-store',
        'Pragma': 'no-cache',
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)

def inteiro(valor):
    if isinstance(valor, bool):
        raise ValueError('booleano não é contagem')
    if isinstance(valor, int):
        return valor
    if isinstance(valor, str) and valor.isdigit():
        return int(valor)
    raise ValueError(f'Contagem inválida: {valor!r}')

def candidatos_do_documento(doc):
    cargos = [c for c in doc.get('carg', []) if str(c.get('cd')) == '1']
    if len(cargos) != 1:
        raise ValueError('Cargo de presidente ausente ou duplicado')
    candidatos = []
    for agr in cargos[0].get('agr', []):
        for par in agr.get('par', []):
            for cand in par.get('cand', []):
                candidatos.append({'numero': str(cand['n']),
                                   'nome': cand.get('nmu') or cand.get('nm'),
                                   'votos': inteiro(cand['vap'])})
    if len(candidatos) != 2 or len({c['numero'] for c in candidatos}) != 2:
        raise ValueError('Candidatos presidenciais ausentes, duplicados ou inesperados')
    return candidatos

def ler_anterior():
    try:
        anterior = json.loads(OUT.read_text(encoding='utf-8'))
        if anterior.get('status') in ('sem_totalizacao', 'parcial_validada'):
            return anterior
    except (OSError, ValueError, TypeError):
        pass
    return None

def main():
    DATA.mkdir(parents=True, exist_ok=True)
    agora = datetime.now(timezone.utc).isoformat()
    anterior = ler_anterior()
    saida = {'status': 'indisponivel', 'consultado_em_utc': agora,
             'fonte': 'TSE - EA11/EA20', 'configuracao_acessivel': False,
             'dados': None, 'mensagem': 'Aguardando resposta e validação do TSE.'}
    try:
        cfg = fetch(CONFIG)
        saida['configuracao_acessivel'] = True
        pleitos = [p for p in cfg.get('pl', []) if str(p.get('cd')) == '3221' and p.get('c') == 'ele2026']
        if len(pleitos) != 1:
            raise ValueError('Pleito 3221 não identificado univocamente no EA11')
        eleicoes = [e for e in pleitos[0].get('e', [])
                    if str(e.get('cd')) == '6258' and str(e.get('t')) == '2'
                    and any(a.get('cd') == 'br' and any(str(c.get('cd')) == '1' for c in a.get('cp', []))
                            for a in e.get('abr', []))]
        if len(eleicoes) != 1:
            raise ValueError('Eleição presidencial 6258 não confirmada no EA11')
        url = f"{BASE}/{pleitos[0]['c']}/6258/dados/br/br-c0001-e006258-u.json"
        doc = fetch(url)
        saida['url_resultado'] = url
        if str(doc.get('ele')) != '6258' or str(doc.get('t')) != '2' or doc.get('cdabr') != 'br':
            raise ValueError('EA20 retornou eleição, turno ou abrangência incompatíveis')
        candidatos = candidatos_do_documento(doc)
        s, v, e = doc.get('s', {}), doc.get('v', {}), doc.get('e', {})
        total_secoes, secoes, validos = inteiro(s['ts']), inteiro(s['st']), inteiro(v['vv'])
        if not 0 <= secoes <= total_secoes or sum(c['votos'] for c in candidatos) != validos:
            raise ValueError('Contagens inconsistentes no EA20')
        if anterior and anterior.get('dados'):
            antigo = anterior['dados']
            if (secoes < antigo.get('secoes_totalizadas', 0)
                    or validos < antigo.get('votos_validos', 0)):
                raise ValueError('Regressão de totalização: resposta aparentemente antiga')
        candidatos.sort(key=lambda c: c['votos'], reverse=True)
        for c in candidatos:
            c['percentual'] = round(c['votos'] * 100 / validos, 2) if validos else None
        dados = {'eleicao': '6258', 'turno': 2, 'secoes_totalizadas': secoes,
                 'secoes_total': total_secoes, 'votos_validos': validos,
                 'candidatos': candidatos,
                 'diferenca_votos': abs(candidatos[0]['votos'] - candidatos[1]['votos']),
                 'eleitorado_apto': inteiro(e['te']), 'comparecimento': inteiro(e['c']),
                 'abstencoes': inteiro(e['a']), 'brancos': inteiro(v['vb']),
                 'nulos': inteiro(v['vn']),
                 'gerado_em_tse': f"{doc.get('dg', '')} {doc.get('hg', '')}".strip(),
                 'idg': str(doc.get('idg', ''))}
        por_uf, erros_uf = {}, {}
        for uf in UFS:
            try:
                du = fetch(f'{BASE}/ele2026/6258/dados/{uf}/{uf}-c0001-e006258-u.json')
                if (str(du.get('ele')) != '6258' or str(du.get('t')) != '2'
                        or str(du.get('cdabr')).lower() != uf):
                    raise ValueError('Identificação de UF/eleição incompatível')
                cs = candidatos_do_documento(du)
                votos_uf = {c['numero']: c['votos'] for c in cs}
                if set(votos_uf) != {c['numero'] for c in candidatos}:
                    raise ValueError('Candidatos inconsistentes')
                su, vu = du.get('s', {}), du.get('v', {})
                ts, st, vv = inteiro(su['ts']), inteiro(su['st']), inteiro(vu['vv'])
                if not 0 <= st <= ts or sum(votos_uf.values()) != vv:
                    raise ValueError('Totalizações inconsistentes')
                por_uf[uf.upper()] = {'secoes_totalizadas': st, 'secoes_total': ts,
                                     'votos_validos': vv, 'candidatos': votos_uf,
                                     'gerado_em_tse': f"{du.get('dg', '')} {du.get('hg', '')}".strip()}
            except Exception as exc:
                erros_uf[uf.upper()] = type(exc).__name__
        dados['por_uf'] = por_uf
        dados['ufs_validadas'] = len(por_uf)
        dados['ufs_pendentes'] = sorted(erros_uf)
        dados['regioes'] = {}
        for nome, siglas in REGIOES.items():
            estados = siglas.upper().split()
            if all(uf in por_uf for uf in estados):
                dados['regioes'][nome] = {
                    'secoes_totalizadas': sum(por_uf[u]['secoes_totalizadas'] for u in estados),
                    'secoes_total': sum(por_uf[u]['secoes_total'] for u in estados),
                    'votos_validos': sum(por_uf[u]['votos_validos'] for u in estados),
                    'candidatos': {c['numero']: sum(por_uf[u]['candidatos'][c['numero']] for u in estados)
                                   for c in candidatos}}
        saida['dados'] = dados
        saida['status'] = 'sem_totalizacao' if secoes == 0 else 'parcial_validada'
        saida['mensagem'] = ('Arquivo oficial validado. Sem seções totalizadas nesta consulta.'
                            if secoes == 0 else 'Resultado nacional oficial disponível (EA20).')
        print(saida['mensagem'], '| seções:', secoes, '| votos válidos:', validos,
              '| gerado pelo TSE:', dados['gerado_em_tse'])
    except Exception as exc:
        saida['erro_tipo'] = type(exc).__name__
        saida['mensagem'] = 'Não foi possível validar uma nova consulta ao TSE.'
        if anterior and anterior.get('dados'):
            saida['status'] = 'dados_anteriores'
            saida['dados'] = anterior['dados']
            saida['ultima_consulta_validada_utc'] = anterior.get('consultado_em_utc')
            saida['mensagem'] += ' Mantida última parcial validada; pode estar desatualizada.'
        print('Consulta sem validação:', type(exc).__name__, str(exc)[:240])
    OUT.write_text(json.dumps(saida, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if saida['status'] in ('sem_totalizacao', 'parcial_validada') and saida['dados'] is not None:
        try:
            hist = json.loads(HIST.read_text(encoding='utf-8')) if HIST.exists() else []
            if not isinstance(hist, list):
                hist = []
            d = saida['dados']
            registro = {'consultado_em_utc': agora, 'gerado_em_tse': d['gerado_em_tse'],
                        'secoes_totalizadas': d['secoes_totalizadas'],
                        'votos_validos': d['votos_validos'],
                        'candidatos': {c['numero']: c['votos'] for c in d['candidatos']}}
            chave = lambda r: (r.get('gerado_em_tse'), r.get('secoes_totalizadas'),
                               r.get('votos_validos'), r.get('candidatos'))
            if not hist or chave(hist[-1]) != chave(registro):
                hist.append(registro)
                HIST.write_text(json.dumps(hist[-1000:], ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        except (OSError, ValueError, TypeError) as exc:
            print('Histórico não gravado:', type(exc).__name__)

if __name__ == '__main__':
    main()
