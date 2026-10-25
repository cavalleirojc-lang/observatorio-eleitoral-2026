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
HIST_UF = DATA / 'historico_ufs.json'
PROJECAO = DATA / 'projecao.json'
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


def atualizar_historico_ufs(dados, agora):
    """Registra somente mudanças estaduais; preserva amostras antigas e rejeita regressões."""
    historico = json.loads(HIST_UF.read_text(encoding='utf-8')) if HIST_UF.exists() else []
    if not isinstance(historico, list):
        raise ValueError('Histórico estadual inválido')
    anterior_por_uf = {}
    for registro in historico:
        if not isinstance(registro, dict):
            raise ValueError('Registro estadual inválido')
        for uf, v in registro.get('ufs', {}).items():
            anterior_por_uf[uf] = v
    novos = {}
    for uf, atual in dados.get('por_uf', {}).items():
        anterior = anterior_por_uf.get(uf)
        if anterior and (atual['secoes_totalizadas'] < anterior['secoes_totalizadas'] or
                         atual['votos_validos'] < anterior['votos_validos']):
            continue
        if not anterior or any(atual[k] != anterior.get(k) for k in
                               ('secoes_totalizadas', 'votos_validos', 'candidatos')):
            novos[uf] = atual
    if not novos:
        return False
    historico.append({'consultado_em_utc': agora,
                      'gerado_em_tse_nacional': dados['gerado_em_tse'],
                      'ufs': novos})
    salvar_json(HIST_UF, historico[-1200:])
    return True


def projetar(dados, comparativos):
    """Cenários exploratórios por UF. Sem calibração retrospectiva: NÃO publicar probabilidade validada."""
    import random
    import statistics
    primeiro = comparativos.get('primeiro_turno_2026_por_uf', {})
    atuais = dados.get('por_uf', {})
    try:
        historico = json.loads(HIST_UF.read_text(encoding='utf-8')) if HIST_UF.exists() else []
    except (OSError, ValueError):
        historico = []
    anteriores = {}
    for r in historico:
        for uf, valor in r.get('ufs', {}).items():
            anteriores.setdefault(uf, []).append(valor)
    linhas = []
    for uf in UFS:
        u = atuais.get(uf.upper())
        p = primeiro.get(uf.upper())
        if not u or not p or not u.get('secoes_total') or not p.get('votos_validos'):
            continue
        total, apuradas = u['secoes_total'], u['secoes_totalizadas']
        if not 0 <= apuradas <= total:
            continue
        a = u['candidatos'].get('13', 0)
        b = u['candidatos'].get('22', 0)
        vv = a + b
        frac = apuradas / total
        if vv == 0 and frac > 0:
            continue
        # A estimativa de votos restantes usa a escala estadual do primeiro turno.
        # A densidade por seção já apurada recebe peso crescente com o progresso.
        ref_restante = max(0.0, p['votos_validos'] * (1-frac))
        por_secao = vv / apuradas if apuradas else 0
        estimativa_local = max(0.0, por_secao * (total-apuradas))
        peso_local = min(0.8, frac) if apuradas else 0
        restante = (1-peso_local)*ref_restante + peso_local*estimativa_local
        restante = min(restante, p['votos_validos']*1.5)
        base = a/vv if vv else .5
        ultimo = None
        for ant in reversed(anteriores.get(uf.upper(), [])[:-1]):
            aa = ant.get('candidatos', {}).get('13')
            bb = ant.get('candidatos', {}).get('22')
            if isinstance(aa, int) and isinstance(bb, int):
                da, db = a-aa, b-bb
                if da >= 0 and db >= 0 and da+db >= max(100, vv*.002):
                    ultimo = da/(da+db)
                    break
        # Shrinkage da última parcial: variações recentes não devem dominar a projeção.
        centro = .75*base + .25*ultimo if ultimo is not None else base
        # Incerteza heurística: maior com pouca apuração e pouca história estadual.
        sigma = .025 + .10*(1-frac)
        linhas.append({'uf':uf.upper(), 'apuracao_pct':round(frac*100,2),
                       'votos_restantes_estimados':round(restante),
                       'participacao_13_observada':round(base*100,2),
                       'participacao_13_projetada':round(centro*100,2),
                       'intervalos_estaduais_disponiveis':len(anteriores.get(uf.upper(), [])),
                       '_rest':restante,'_centro':centro,'_sigma':sigma})
    base_resultado = {'metodo':'extrapolacao_uf_com_shrinkage_e_choques',
                      'status':'insuficiente', 'uf_utilizadas':len(linhas),
                      'aviso':('Frequências simuladas condicionais a hipóteses não calibradas. '
                               'NÃO são probabilidades de vitória validadas nem previsão oficial.'),
                      'validacao_retroativa':'pendente',
                      'ufs':[{k:v for k,v in x.items() if not k.startswith('_')} for x in linhas]}
    if len(linhas) != 27:
        base_resultado['motivo']='Necessários dados atuais e referência do 1º turno para as 27 UFs.'
        return base_resultado
    a0 = next(c['votos'] for c in dados['candidatos'] if c['numero']=='13')
    b0 = next(c['votos'] for c in dados['candidatos'] if c['numero']=='22')
    if sum(x['votos_restantes_estimados'] for x in linhas) < 1:
        base_resultado.update(status='concluido', vencedor_observado='13' if a0>b0 else '22' if b0>a0 else 'empate',
                              total_final_13=a0,total_final_22=b0,
                              votos_restantes_estimados=0)
        return base_resultado
    rng=random.Random(20261025)
    amostras=[]
    for _ in range(4000):
        choque_nacional=rng.gauss(0,.035)
        va,vb=float(a0),float(b0)
        for x in linhas:
            # Erro comum de participação, erro por UF e variação de comparecimento.
            participacao=max(.01,min(.99,x['_centro']+choque_nacional+rng.gauss(0,x['_sigma'])))
            rest=max(0,x['_rest']*(1+rng.gauss(0,.07)))
            va+=rest*participacao
            vb+=rest*(1-participacao)
        amostras.append((va,vb))
    sa=sorted(v[0] for v in amostras);sb=sorted(v[1] for v in amostras)
    dif=sorted(v[0]-v[1] for v in amostras)
    vencedor_atual='13' if a0>b0 else '22' if b0>a0 else 'empate'
    base_resultado.update(
        status='exploratorio', simulacoes=len(amostras),
        votos_restantes_estimados=round(sum(x['_rest'] for x in linhas)),
        total_final_13=round(statistics.median(sa)),
        total_final_22=round(statistics.median(sb)),
        diferenca_mediana_13_menos_22=round(statistics.median(dif)),
        faixa_cenarios_13=[round(sa[100]),round(sa[-101])],
        faixa_cenarios_22=[round(sb[100]),round(sb[-101])],
        faixa_cenarios_diferenca=[round(dif[100]),round(dif[-101])],
        frequencia_cenarios_13_pct=round(100*sum(v[0]>v[1] for v in amostras)/len(amostras),2),
        frequencia_cenarios_22_pct=round(100*sum(v[1]>v[0] for v in amostras)/len(amostras),2),
        frequencia_cenarios_virada_pct=round(100*sum((v[0]>v[1]) != (vencedor_atual=='13')
                                            for v in amostras)/len(amostras),2)
        if vencedor_atual!='empate' else None,
        observacao_faixas='Intervalo central de 95% dos cenários simulados, NÃO intervalo de confiança calibrado.')
    return base_resultado


# Referência nacional de 2022, publicada pelo próprio TSE após a totalização.
# Não atribuir os votos do candidato 22 de 2022 ao candidato 22 de 2026:
# o número da candidatura não é uma identidade histórica estável.
REFERENCIA_2022 = {
    'ano': 2022, 'turno': 2, 'abrangencia': 'BR',
    'situacao': 'resultado_final_publicado_pelo_tse',
    'votos_validos': 118552353,
    'comparecimento': 124252796,
    'abstencoes': 32200558,
    'brancos': 1769678,
    'nulos': 3930765,
    'candidatos': [
        {'numero': '13', 'nome': 'Luiz Inácio Lula da Silva', 'votos': 60345999},
        {'numero': '22', 'nome': 'Jair Bolsonaro', 'votos': 58206354},
    ],
    'fonte': 'https://www.tse.jus.br/comunicacao/noticias/2022/Outubro/100-das-secoes-totalizadas-confira-como-ficou-o-quadro-eleitoral-apos-o-2o-turno/',
    'nota': 'Referência nacional publicada pelo TSE; não contém recortes estaduais.',
}


def candidatos_primeiro_turno(doc):
    """Lê todos os candidatos presidenciais, inclusive os de votação zero."""
    cargos = [c for c in doc.get('carg', []) if str(c.get('cd')) == '1']
    if len(cargos) != 1:
        raise ValueError('Cargo presidencial do primeiro turno ausente ou duplicado')
    resultado = []
    for agr in cargos[0].get('agr', []):
        for partido in agr.get('par', []):
            for cand in partido.get('cand', []):
                resultado.append({
                    'numero': str(cand['n']),
                    'nome': cand.get('nmu') or cand.get('nm') or str(cand['n']),
                    'votos': inteiro(cand['vap']),
                })
    if not resultado or len({c['numero'] for c in resultado}) != len(resultado):
        raise ValueError('Lista de candidatos do primeiro turno inválida')
    return sorted(resultado, key=lambda c: c['votos'], reverse=True)


def primeiro_turno_2026(abrangencia):
    codigo = abrangencia.lower()
    url = f'{BASE}/ele2026/6257/dados/{codigo}/{codigo}-c0001-e006257-u.json'
    doc = fetch(url)
    if (str(doc.get('ele')) != '6257' or str(doc.get('t')) != '1'
            or str(doc.get('cdabr', '')).lower() != codigo):
        raise ValueError(f'Identificação do primeiro turno incompatível: {codigo}')
    candidatos = candidatos_primeiro_turno(doc)
    validos = inteiro(doc['v']['vv'])
    if sum(c['votos'] for c in candidatos) != validos:
        raise ValueError(f'Votos válidos do primeiro turno inconsistentes: {codigo}')
    secoes = inteiro(doc['s']['st'])
    total = inteiro(doc['s']['ts'])
    if secoes > total:
        raise ValueError(f'Seções do primeiro turno inconsistentes: {codigo}')
    return {
        'ano': 2026, 'turno': 1, 'abrangencia': codigo.upper(),
        'situacao': 'conforme_arquivo_oficial_consultado',
        'secoes_totalizadas': secoes, 'secoes_total': total,
        'votos_validos': validos, 'candidatos': candidatos,
        'eleitorado_apto': inteiro(doc['e']['te']),
        'comparecimento': inteiro(doc['e']['c']),
        'abstencoes': inteiro(doc['e']['a']),
        'brancos': inteiro(doc['v']['vb']), 'nulos': inteiro(doc['v']['vn']),
        'gerado_em_tse': horario_tse(doc), 'fonte': url,
    }


def obter_comparativos():
    """Comparativos são opcionais: uma falha histórica não derruba a apuração."""
    comparativos = {
        'referencia_2022_segundo_turno': REFERENCIA_2022,
        'primeiro_turno_2026': None,
        'primeiro_turno_2026_por_uf': {},
        'primeiro_turno_2026_regioes': {},
        'pendencias': [],
        'observacao': ('Os dados de 2022 são nacionais. As comparações entre turnos '
                       'devem identificar os candidatos por nome, não apenas por número. '
                       'Uma parcial de 2026 não equivale a um resultado final.'),
    }
    try:
        comparativos['primeiro_turno_2026'] = primeiro_turno_2026('br')
    except Exception as exc:
        comparativos['pendencias'].append('BR: ' + type(exc).__name__)
    for uf in UFS:
        try:
            comparativos['primeiro_turno_2026_por_uf'][uf.upper()] = primeiro_turno_2026(uf)
        except Exception as exc:
            comparativos['pendencias'].append(uf.upper() + ': ' + type(exc).__name__)
    for nome, siglas in REGIOES.items():
        estados = siglas.upper().split()
        if not all(uf in comparativos['primeiro_turno_2026_por_uf'] for uf in estados):
            continue
        fontes = [comparativos['primeiro_turno_2026_por_uf'][uf] for uf in estados]
        numeros = sorted({c['numero'] for f in fontes for c in f['candidatos']})
        comparativos['primeiro_turno_2026_regioes'][nome] = {
            'votos_validos': sum(f['votos_validos'] for f in fontes),
            'comparecimento': sum(f['comparecimento'] for f in fontes),
            'abstencoes': sum(f['abstencoes'] for f in fontes),
            'brancos': sum(f['brancos'] for f in fontes),
            'nulos': sum(f['nulos'] for f in fontes),
            'candidatos': {n: sum(next((c['votos'] for c in f['candidatos']
                                       if c['numero'] == n), 0) for f in fontes)
                           for n in numeros},
        }
    return comparativos


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
    # Referências históricas independentes da coleta atual.
    # Preservar referências anteriores se a CDN estiver indisponível.
    try:
        comparativos = obter_comparativos()
        if anterior and isinstance(anterior.get('comparativos'), dict):
            anterior_comp = anterior['comparativos']
            if comparativos['primeiro_turno_2026'] is None:
                comparativos['primeiro_turno_2026'] = anterior_comp.get('primeiro_turno_2026')
            for campo in ('primeiro_turno_2026_por_uf', 'primeiro_turno_2026_regioes'):
                antigos = anterior_comp.get(campo, {})
                if isinstance(antigos, dict):
                    comparativos[campo] = {**antigos, **comparativos[campo]}
        saida['comparativos'] = comparativos
        print('Comparativos: 1º turno BR:', bool(comparativos['primeiro_turno_2026']),
              '| UFs:', len(comparativos['primeiro_turno_2026_por_uf']),
              '| pendências:', len(comparativos['pendencias']))
    except Exception as exc:
        print('Comparativos indisponíveis:', type(exc).__name__, str(exc)[:160])
        if anterior and isinstance(anterior.get('comparativos'), dict):
            saida['comparativos'] = anterior['comparativos']
    salvar_json(OUT, saida)
    if saida['status'] in ('sem_totalizacao', 'parcial_validada') and saida['dados'] is not None:
        try:
            novo = atualizar_historico(saida['dados'], agora)
            print('Histórico:', 'nova parcial registrada' if novo else 'sem nova parcial')
        except (OSError, ValueError, TypeError) as exc:
            print('Histórico não gravado:', type(exc).__name__, str(exc)[:160])
        try:
            if atualizar_historico_ufs(saida['dados'], agora):
                print('Histórico estadual: novas parciais registradas')
            comp=saida.get('comparativos', {})
            resultado=projetar(saida['dados'],comp)
            resultado['consultado_em_utc']=agora
            resultado['gerado_em_tse']=saida['dados']['gerado_em_tse']
            salvar_json(PROJECAO,resultado)
            print('Projeção:',resultado['status'],'| UFs:',resultado['uf_utilizadas'])
        except (OSError, ValueError, TypeError, KeyError) as exc:
            print('Projeção não atualizada:',type(exc).__name__,str(exc)[:160])


if __name__ == '__main__':
    main()
