"""Observatório Eleitoral 2026 — coleta EA11/EA20 com auditoria e histórico.

Mantém o contrato de data/atual.json consumido pelo site. Não mistura
resultados de diferentes turnos e não substitui a última parcial validada
por dados inválidos. Usa apenas endpoints oficiais do TSE.
"""
import json
import time
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
REGIOES = {
    'Norte': 'ac ap am pa ro rr to',
    'Nordeste': 'al ba ce ma pb pe pi rn se',
    'Centro-Oeste': 'df go mt ms',
    'Sudeste': 'es mg rj sp',
    'Sul': 'pr rs sc',
}


def fetch(url):
    sep = '&' if '?' in url else '?'
    url = url + sep + urllib.parse.urlencode({'_ts': time.time_ns()})
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (compatible; ObservatorioEleitoral2026/4.0)',
        'Accept': 'application/json',
        'Cache-Control': 'no-cache, no-store',
        'Pragma': 'no-cache',
    })
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def inteiro(value):
    if isinstance(value, bool):
        raise ValueError('Booleano não é contagem')
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise ValueError(f'Contagem inválida: {value!r}')


def candidatos_do_documento(doc):
    cargos = [c for c in doc.get('carg', []) if str(c.get('cd')) == '1']
    if len(cargos) != 1:
        raise ValueError('Cargo presidencial ausente ou duplicado')
    candidatos = []
    for agr in cargos[0].get('agr', []):
        for partido in agr.get('par', []):
            for cand in partido.get('cand', []):
                candidatos.append({
                    'numero': str(cand['n']),
                    'nome': cand.get('nmu') or cand.get('nm') or str(cand['n']),
                    'votos': inteiro(cand['vap']),
                })
    if len(candidatos) != 2 or len({c['numero'] for c in candidatos}) != 2:
        raise ValueError('Candidatos presidenciais ausentes, duplicados ou inesperados')
    return candidatos


def ler_anterior():
    try:
        doc = json.loads(OUT.read_text(encoding='utf-8'))
        if isinstance(doc, dict) and doc.get('dados') and doc.get('status') in (
            'parcial_validada', 'sem_totalizacao', 'dados_anteriores'
        ):
            return doc
    except (OSError, ValueError, TypeError):
        pass
    return None


def salvar_json(path, value):
    """Grava arquivo temporário e substitui o destino de forma atômica."""
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp.replace(path)


def horario_tse(doc):
    return f"{doc.get('dg', '')} {doc.get('hg', '')}".strip()


def validar_identidade(doc, abrangencia):
    if (str(doc.get('ele')) != '6258' or str(doc.get('t')) != '2'
            or str(doc.get('cdabr', '')).lower() != abrangencia):
        raise ValueError(f'Identificação de eleição/turno/abrangência incompatível: {abrangencia}')


def validar_configuracao():
    cfg = fetch(CONFIG)
    pleitos = [p for p in cfg.get('pl', [])
               if str(p.get('cd')) == '3221' and p.get('c') == 'ele2026']
    if len(pleitos) != 1:
        raise ValueError('Pleito 3221 não identificado univocamente no EA11')
    eleicoes = [e for e in pleitos[0].get('e', [])
                if str(e.get('cd')) == '6258' and str(e.get('t')) == '2'
                and any(a.get('cd') == 'br'
                        and any(str(c.get('cd')) == '1' for c in a.get('cp', []))
                        for a in e.get('abr', []))]
    if len(eleicoes) != 1:
        raise ValueError('Eleição presidencial 6258 não confirmada no EA11')


def registro_historico(dados, agora):
    return {
        'consultado_em_utc': agora,
        'gerado_em_tse': dados['gerado_em_tse'],
        'secoes_totalizadas': dados['secoes_totalizadas'],
        'votos_validos': dados['votos_validos'],
        'candidatos': {c['numero']: c['votos'] for c in dados['candidatos']},
    }


def chave_historico(r):
    return (r.get('gerado_em_tse'), r.get('secoes_totalizadas'),
            r.get('votos_validos'), json.dumps(r.get('candidatos', {}), sort_keys=True))


def atualizar_historico(dados, agora):
    # Arquivos malformados não devem ser sobrescritos silenciosamente.
    hist = json.loads(HIST.read_text(encoding='utf-8')) if HIST.exists() else []
    if not isinstance(hist, list) or any(not isinstance(r, dict) for r in hist):
        raise ValueError('Histórico existente possui formato inválido')
    registro = registro_historico(dados, agora)
    if any(chave_historico(r) == chave_historico(registro) for r in hist):
        return False
    hist.append(registro)
    salvar_json(HIST, hist[-1000:])
    return True


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    agora = datetime.now(timezone.utc).isoformat()
    anterior = ler_anterior()
    saida = {
        'status': 'indisponivel', 'consultado_em_utc': agora,
        'fonte': 'TSE - EA11/EA20', 'configuracao_acessivel': False,
        'dados': None, 'mensagem': 'Aguardando resposta e validação do TSE.',
    }
    try:
        validar_configuracao()
        saida['configuracao_acessivel'] = True
        url = f'{BASE}/ele2026/6258/dados/br/br-c0001-e006258-u.json'
        doc = fetch(url)
        saida['url_resultado'] = url
        validar_identidade(doc, 'br')
        candidatos = candidatos_do_documento(doc)
        s, v, e = doc.get('s', {}), doc.get('v', {}), doc.get('e', {})
        total, secoes, validos = inteiro(s['ts']), inteiro(s['st']), inteiro(v['vv'])
        if not 0 <= secoes <= total or sum(c['votos'] for c in candidatos) != validos:
            raise ValueError('Contagens nacionais inconsistentes no EA20')
        if anterior and anterior.get('dados'):
            antigo = anterior['dados']
            if (secoes < antigo.get('secoes_totalizadas', 0)
                    or validos < antigo.get('votos_validos', 0)):
                raise ValueError('Regressão de totalização: resposta aparentemente antiga')
        candidatos.sort(key=lambda c: c['votos'], reverse=True)
        for c in candidatos:
            c['percentual'] = round(100 * c['votos'] / validos, 2) if validos else None
        dados = {
            'eleicao': '6258', 'turno': 2,
            'secoes_totalizadas': secoes, 'secoes_total': total,
            'votos_validos': validos, 'candidatos': candidatos,
            'diferenca_votos': abs(candidatos[0]['votos'] - candidatos[1]['votos']),
            'eleitorado_apto': inteiro(e['te']), 'comparecimento': inteiro(e['c']),
            'abstencoes': inteiro(e['a']), 'brancos': inteiro(v['vb']),
            'nulos': inteiro(v['vn']), 'gerado_em_tse': horario_tse(doc),
            'idg': str(doc.get('idg', '')),
        }
        por_uf, erros_uf = {}, {}
        for uf in UFS:
            try:
                du = fetch(f'{BASE}/ele2026/6258/dados/{uf}/{uf}-c0001-e006258-u.json')
                validar_identidade(du, uf)
                cs = candidatos_do_documento(du)
                votos_uf = {c['numero']: c['votos'] for c in cs}
                if set(votos_uf) != {c['numero'] for c in candidatos}:
                    raise ValueError('Candidatos inconsistentes com resultado nacional')
                su, vu = du.get('s', {}), du.get('v', {})
                ts, st, vv = inteiro(su['ts']), inteiro(su['st']), inteiro(vu['vv'])
                if not 0 <= st <= ts or sum(votos_uf.values()) != vv:
                    raise ValueError('Totalizações inconsistentes')
                por_uf[uf.upper()] = {
                    'secoes_totalizadas': st, 'secoes_total': ts,
                    'votos_validos': vv, 'candidatos': votos_uf,
                    'gerado_em_tse': horario_tse(du),
                }
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
                    'candidatos': {c['numero']: sum(por_uf[u]['candidatos'][c['numero']]
                                                    for u in estados) for c in candidatos},
                }
        # Diferenças podem decorrer de momentos de atualização distintos.
        # Não marcar como erro definitivo sem verificar sincronização dos arquivos.
        somas = {
            'secoes_totalizadas': sum(x['secoes_totalizadas'] for x in por_uf.values()),
            'votos_validos': sum(x['votos_validos'] for x in por_uf.values()),
            'candidatos': {c['numero']: sum(x['candidatos'][c['numero']]
                                            for x in por_uf.values()) for c in candidatos},
        }
        completo = len(por_uf) == 27
        diferenças = {
            'secoes_totalizadas': secoes - somas['secoes_totalizadas'],
            'votos_validos': validos - somas['votos_validos'],
            'candidatos': {c['numero']: c['votos'] - somas['candidatos'][c['numero']]
                           for c in candidatos},
        }
        dados['auditoria'] = {
            'ufs_consultadas': 27, 'ufs_validadas': len(por_uf),
            'ufs_pendentes': sorted(erros_uf), 'erros_uf': erros_uf,
            'soma_ufs': somas, 'diferenca_nacional_menos_ufs': diferenças,
            'comparacao_completa': completo,
            'totais_coincidem': completo and all([
                diferenças['secoes_totalizadas'] == 0,
                diferenças['votos_validos'] == 0,
                all(x == 0 for x in diferenças['candidatos'].values()),
            ]),
            'observacao': ('Arquivos nacionais e estaduais podem ter horários de geração distintos; '
                           'diferenças não comprovam erro de totalização.'),
        }
        saida['dados'] = dados
        saida['status'] = 'sem_totalizacao' if secoes == 0 else 'parcial_validada'
        saida['mensagem'] = ('Arquivo oficial validado. Sem seções totalizadas nesta consulta.'
                            if secoes == 0 else 'Resultado nacional oficial disponível (EA20).')
        print(saida['mensagem'], '| seções:', secoes, '| votos válidos:', validos,
              '| gerado pelo TSE:', dados['gerado_em_tse'])
        print('Auditoria: UFs validadas:', len(por_uf),
              '| coincidência nacional/UF:', dados['auditoria']['totais_coincidem'])
    except Exception as exc:
        saida['erro_tipo'] = type(exc).__name__
        saida['mensagem'] = 'Não foi possível validar uma nova consulta ao TSE.'
        if anterior and anterior.get('dados'):
            saida['status'] = 'dados_anteriores'
            saida['dados'] = anterior['dados']
            saida['ultima_consulta_validada_utc'] = (
                anterior.get('ultima_consulta_validada_utc')
                if anterior.get('status') == 'dados_anteriores'
                else anterior.get('consultado_em_utc')
            )
            saida['mensagem'] += ' Mantida última parcial validada; pode estar desatualizada.'
        print('Consulta sem validação:', type(exc).__name__, str(exc)[:240])
    salvar_json(OUT, saida)
    if saida['status'] in ('sem_totalizacao', 'parcial_validada') and saida['dados'] is not None:
        try:
            novo = atualizar_historico(saida['dados'], agora)
            print('Histórico:', 'nova parcial registrada' if novo else 'sem nova parcial')
        except (OSError, ValueError, TypeError) as exc:
            print('Histórico não gravado:', type(exc).__name__, str(exc)[:160])


if __name__ == '__main__':
    main()
