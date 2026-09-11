"""Calendário-base 2026 para o MVP QS Ajuda de Custos.

Escopo: feriados nacionais + feriados estaduais/distritais conhecidos para 2026.
Feriados municipais são mantidos separadamente e podem ser cadastrados no QS.
Pontos facultativos federais aparecem no calendário, mas NÃO retiram dia do cálculo por padrão.
"""

UF_NAMES = {
    "AC":"Acre","AL":"Alagoas","AP":"Amapá","AM":"Amazonas","BA":"Bahia","CE":"Ceará",
    "DF":"Distrito Federal","ES":"Espírito Santo","GO":"Goiás","MA":"Maranhão","MT":"Mato Grosso",
    "MS":"Mato Grosso do Sul","MG":"Minas Gerais","PA":"Pará","PB":"Paraíba","PR":"Paraná",
    "PE":"Pernambuco","PI":"Piauí","RJ":"Rio de Janeiro","RN":"Rio Grande do Norte",
    "RS":"Rio Grande do Sul","RO":"Rondônia","RR":"Roraima","SC":"Santa Catarina","SP":"São Paulo",
    "SE":"Sergipe","TO":"Tocantins",
}

NATIONAL_HOLIDAYS_2026 = [
    ("2026-01-01", "Confraternização Universal"),
    ("2026-04-03", "Paixão de Cristo"),
    ("2026-04-21", "Tiradentes"),
    ("2026-05-01", "Dia Mundial do Trabalho"),
    ("2026-09-07", "Independência do Brasil"),
    ("2026-10-12", "Nossa Senhora Aparecida"),
    ("2026-11-02", "Finados"),
    ("2026-11-15", "Proclamação da República"),
    ("2026-11-20", "Dia Nacional de Zumbi e da Consciência Negra"),
    ("2026-12-25", "Natal"),
]

# Visíveis como referência, mas sem impacto automático na ajuda de custos.
FEDERAL_OPTIONAL_DAYS_2026 = [
    ("2026-02-16", "Carnaval — ponto facultativo"),
    ("2026-02-17", "Carnaval — ponto facultativo"),
    ("2026-02-18", "Quarta-feira de Cinzas — ponto facultativo até 14h"),
    ("2026-06-04", "Corpus Christi — ponto facultativo federal"),
    ("2026-10-28", "Dia do Servidor Público federal — ponto facultativo"),
    ("2026-12-24", "Véspera de Natal — ponto facultativo após 13h"),
    ("2026-12-31", "Véspera de Ano Novo — ponto facultativo após 13h"),
]

# Datas estaduais/distritais específicas. Os 10 feriados nacionais acima se aplicam a todas as UFs.
STATE_HOLIDAYS_2026 = {
    "AC": [
        ("2026-01-22", "Dia do Católico — observância transferida em 2026", "Data legal 20/01; calendário estadual de 2026 transfere a observância."),
        ("2026-01-23", "Dia do Evangélico", None),
        ("2026-03-08", "Dia Internacional da Mulher", None),
        ("2026-06-15", "Aniversário do Estado do Acre", None),
        ("2026-09-05", "Dia da Amazônia", None),
        ("2026-11-17", "Tratado de Petrópolis", None),
    ],
    "AL": [
        ("2026-06-24", "São João", None),
        ("2026-06-29", "São Pedro", None),
        ("2026-09-16", "Emancipação Política de Alagoas", None),
        ("2026-11-30", "Dia Estadual do Evangélico", None),
    ],
    "AP": [
        ("2026-03-19", "Dia de São José", None),
        ("2026-05-15", "Dia de Cabralzinho", None),
        ("2026-09-13", "Data Magna do Amapá", None),
    ],
    "AM": [
        ("2026-09-05", "Elevação do Amazonas à Categoria de Província", None),
    ],
    "BA": [("2026-07-02", "Independência da Bahia", None)],
    "CE": [
        ("2026-03-19", "Dia de São José", None),
        ("2026-03-25", "Data Magna do Ceará", None),
    ],
    "DF": [
        ("2026-06-04", "Corpus Christi", "Feriado local do Distrito Federal em 2026."),
        ("2026-11-30", "Dia do Evangélico", None),
    ],
    "ES": [("2026-04-13", "Nossa Senhora da Penha", "Data de observância definida no calendário oficial do ES para 2026.")],
    "GO": [("2026-07-26", "Fundação da Cidade de Goiás", "Feriado estadual; em 2026 cai em domingo.")],
    "MA": [("2026-07-28", "Adesão do Maranhão à Independência do Brasil", None)],
    "MT": [],
    "MS": [("2026-10-11", "Criação do Estado de Mato Grosso do Sul", None)],
    "MG": [],
    "PA": [("2026-08-15", "Adesão do Grão-Pará à Independência do Brasil", None)],
    "PB": [("2026-08-05", "Emancipação Política da Paraíba — Data Magna", None)],
    "PR": [],
    "PE": [("2026-03-06", "Data Magna de Pernambuco", None)],
    "PI": [("2026-10-19", "Dia do Piauí", None)],
    "RJ": [("2026-04-23", "Dia de São Jorge", None)],
    "RN": [("2026-10-03", "Mártires de Cunhaú e Uruaçu", None)],
    "RS": [("2026-09-20", "Revolução Farroupilha", None)],
    "RO": [("2026-01-04", "Criação/Instalação do Estado de Rondônia", None)],
    "RR": [("2026-10-05", "Criação do Estado de Roraima", None)],
    "SC": [("2026-08-16", "Data Magna de Santa Catarina — observância transferida", "Data legal 11/08; em 2026 a observância é transferida para o domingo subsequente.")],
    "SP": [("2026-07-09", "Revolução Constitucionalista de 1932", None)],
    "SE": [("2026-07-08", "Emancipação Política de Sergipe", None)],
    "TO": [
        ("2026-08-15", "Senhor do Bonfim", None),
        ("2026-09-08", "Nossa Senhora da Natividade", None),
        ("2026-10-05", "Criação do Estado do Tocantins", None),
    ],
}

# Referências registradas no banco para auditoria administrativa.
FEDERAL_SOURCE = "Portaria MGI nº 11.460, de 29/12/2025 — calendário federal 2026"
STATE_SOURCE = "Calendário/legislação estadual ou distrital aplicável a 2026 — base homologável QS"
