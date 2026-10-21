# Aura

> IA de análise de investimentos para a B3 — triagem fundamentalista de mercado e parecer técnico com números reais.

**Aura** combina três camadas:

| Camada | Tecnologia | Papel |
|---|---|---|
| **Dados** | Fundamentus + yfinance | Balanço, DRE, 22 indicadores e preços de 395+ papéis da B3 |
| **Filtro** | Python + pandas | Perfis Graham, Bazin, Greenblatt e afins aplicados sobre a B3 inteira |
| **Cérebro** | Gemini (free tier) | Lê o dossiê numérico e escreve o parecer técnico estruturado |

---

## Como funciona

```
                    ┌──────────────────────┐
                    │  Fundamentus (1 req) │
                    │  395 papéis · 22 ind. │
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │  Filtro local (pandas)│  ← escala exata, sem rate limit
                    └──────────┬───────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
        ┌──────────┐    ┌──────────┐    ┌──────────┐
        │ yfinance  │    │ Fundamen-│    │  Gemini  │
        │ preços/   │    │ tus      │    │ (parecer)│
        │ proventos │    │ balanço  │    └──────────┘
        └────┬─────┘    └────┬─────┘
             └───────┬───────┘
                     ▼
              ┌─────────────┐
              │  Parecer IA │  nota · veredito · endividamento ·
              └─────────────┘  geração de caixa · riscos · alertas
```

### Decisões de projeto que valem registro

- **Triagem local, não no servidor.** O formulário de busca avançada do Fundamentus
  mistura escalas: os campos em porcentagem (dividend yield, ROE, margens) expectam
  *fração* (`0.06` = 6%), enquanto a tabela exibe `6,26`. Multiplicadores e razões
  usam valor absoluto. Como a tabela completa vem numa única resposta, filtrar em
  pandas é mais rápido, exato e imune a rate limit.
- **Unidades de FII têm um marcador confiável.** O Fundamentus marca cotas de FII com o
  tipo `ON NM` (contra `ON`, `PN`, `ON N2` de ações e unidades societárias). Elas
  saem do resultado por padrão, porque P/L e EV/EBITDA de fundo não são comparáveis.
- **O prompt exige rastreabilidade.** A IA só pode usar os números fornecidos, precisa
  citar o período de cada um e declarar "n/d" no que faltar. O schema de saída
  força nota, confiança, riscos e lacunas de dados.
- **Cadeia de fallback de modelos.** Na free tier do Gemini há modelos momentaneamente
  congestionados (HTTP 503). O cliente tenta o principal, depois `gemini-3.5-flash`,
  `gemini-3.5-flash-lite` e `gemini-3-flash-preview`.

---

## Rodando localmente

```bash
git clone https://github.com/VectraInsights/Aura.git
cd Aura
python -m venv .venv && .venv\Scripts\activate   # Windows
pip install -r requirements.txt

copy .env.example .env    # Windows
# cp .env.example .env    # Linux/macOS
```

Preencha a chave no `.env`:

```env
GEMINI_API_KEY=sua_chave
GEMINI_MODEL=gemini-3.6-flash
```

Pegue a chave gratuitamente em <https://aistudio.google.com/apikey>.

### API

```bash
uvicorn api.index:app --reload
# http://127.0.0.1:8000  → interface
# http://127.0.0.1:8000/docs → OpenAPI
```

### Linha de comando

```bash
python run.py perfis                                  # lista os perfis
python run.py triagem --perfil dividendario --limite 15
python run.py triagem --perfil graham --json          # saída completa em JSON
python run.py analisar PETR4 --perfil qualidade
python run.py analisar PETR4 VALE3 --perfil dividendario
python run.py contexto PETR4                          # todos os números coletados
python run.py saude                                   # testa chave e modelo
```

---

## Login e perfil do investidor

Sem Supabase e sem banco de dados: **senha única + cookie de sessão assinado**
(`itsdangerous`, que já vem com o Starlette).

| Variável | Para que serve |
|---|---|
| `APP_PASSWORD` | Senha de acesso. **Vazio = aplicação aberta** (sem login). |
| `SESSION_SECRET` | Chave de assinatura do cookie. Sem ela, a sessão morre a cada reinício. |
| `SESSION_MAX_AGE` | Validade da sessão em segundos (padrão 7 dias). |

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"   # gera a SESSION_SECRET
```

O arquivo `.env` é só para uso local. Na Vercel, as duas variáveis vão em
*Settings → Environment Variables* (nos três ambientes).

### Onboarding: a IA pergunta quem você é

Na primeira entrada, o Aura faz um questionário de 9 perguntas — nível de
conhecimento, tolerância a risco, horizonte, objetivo, estratégia de interesse,
universo (ações/FIIs), porte da carteira, dividend yield mínimo e observações
livres.

As respostas ficam no cookie de sessão (assinado) e são **injetadas em cada
parecer**, em duas camadas:

1. **Contexto** — a IA recebe um bloco "Quem é o investidor" com os fatos.
2. **Instruções de estilo** — a IA ajusta o comportamento:

| Resposta | Efeito no parecer |
|---|---|
| Iniciante | Explica cada termo na primeira ocorrência (o que é ROIC, o que é payout), usa analogias |
| Intermediário | Vocabulário técnico, mas explica o que depende de julgamento |
| Avançado | Direto e técnico; foca no não óbvio e nos riscos escondidos |
| Tolerância baixa | Destaca o que pode dar errado e a perda potencial |
| Objetivo renda | Avalia sustentabilidade dos proventos como questão central |
| Objetivo valorização | Foca em margem de segurança e no que precisa mudar no thesis |
| Horizonte curto | Avisa sobre risco de evento, liquidez e janelas de saída ruins |
| Só ações | Ignora FIIs ou avisa quando um papel da lista for na verdade um fundo |

O botão **Meu perfil** (canto superior direito) reabre o questionário a
qualquer momento.

---

## Deploy na Vercel

### Onde a chave do Gemini vai

**Em Environment Variables do projeto Vercel. Nunca no código, nunca no repositório.**

O arquivo `.env` serve só para rodar localmente — e ele está no `.gitignore`, então
nunca vai para o GitHub. Na Vercel, o equivalente é o painel do projeto:

```
Vercel → seu projeto → Settings → Environment Variables → Add Variable
```

| Nome | Valor | Ambiente |
|---|---|---|
| `GEMINI_API_KEY` | sua chave do AI Studio | **Production, Preview e Development** |
| `GEMINI_MODEL` | `gemini-3.6-flash` (opcional) | Production |
| `GEMINI_MODELOS_FALLBACK` | `gemini-3.5-flash,gemini-3.5-flash-lite,gemini-3-flash-preview` (opcional) | Production |

> Marque os três ambientes. Se a variável existir só em "Development", a produção
> responde `503 ia_configurada: false` — a interface sobe, mas nenhum parecer sai.

Para gerar uma chave: <https://aistudio.google.com/apikey> (a cota gratuita é
automática). Depois de mudar uma variável, é preciso **redeploy** — o botão
*Redeploy* do deploy atual.

Verifique o que a aplicação está enxergando:

```bash
curl https://seu-projeto.vercel.app/api/health
# {"status":"ok","ia_configurada":true,"modelo_padrao":"gemini-3.6-flash",...}
```

`"ia_configurada": false` = a variável não chegou no ambiente certo.

### Deployment Protection (a causa mais comum de "Not Found" / 401)

A Vercel pode proteger o projeto com **Vercel Authentication**, que exige login
para acessar qualquer rota — inclusive a API. O sintoma é a interface abrir e as
chamadas falharem.

```
GET /api/perfis  →  401 (XHR)   ou 302 → /login (navegador)
```

Para um projeto pessoal como o Aura, a opção é desligar a proteção:

```
Vercel → seu projeto → Settings → Deployment Protection → Vercel Authentication → Disabled
```

Para deixar o preview aberto e proteger só a produção, use
*Only Production Protection* e adicione um *Protection Bypass for Automation*
(token no header `x-vercel-protection-bypass`), útil para CI.

Como saber se a proteção está ativa:

```bash
curl -H "Accept: application/json" https://SEU-DOMINIO.vercel.app/api/perfis
# 401 + corpo vazio  → proteção ativa
# JSON com "triagem" → tudo certo
```

### Caminho único — integração com o GitHub

A Vercel detecta os pushes e publica sozinha. Não é preciso GitHub Actions.

1. Crie o projeto em <https://vercel.com/new> e conecte o repositório `VectraInsights/Aura`.
2. Cadastre as variáveis de ambiente acima (**Settings → Environment Variables**).
3. Deploy. O `vercel.json` já mapeia `/api/*` para a função Python e o
   `requirements.txt` da raiz instala as dependências.

O build é detectado automaticamente: framework **Other**, build command vazio,
output directory `.`, e a pasta `api/` vira a função serverless.

A partir daí, todo push na `main` vira um deploy — e o commit ganha o selo
"Vercel" no GitHub. Para deploy manual: *Deployments → Redeploy*.

---

## API

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/api/health` | Status, modelo em uso e se a IA está configurada |
| `GET` | `/api/diagnostico` | Diagnóstico do deploy: versão, Python, variáveis presentes e o caminho bruto que chegou à função |
| `GET` | `/api/sessao` | Estado da sessão (autenticado? login ativo? perfil preenchido?) |
| `POST` | `/api/login` | `{"senha": "..."}` — autentica e abre a sessão |
| `POST` | `/api/logout` | Encerra a sessão |
| `GET` | `/api/perfil` | Questionário do onboarding + as respostas dadas |
| `POST` | `/api/perfil` | Salva as respostas do onboarding |
| `GET` | `/api/perfis` | Perfis de triagem e de análise disponíveis |
| `GET` | `/api/triagem?perfil=&limite=&incluir_fiis=` | Varre a B3 (ações e, opcionalmente, FIIs) |
| `GET` | `/api/analise/{ticker}?perfil=` | Parecer técnico da IA sobre uma empresa |
| `POST` | `/api/analise-lote` | `{"tickers": [...], "perfil": "..."}` — até 8 empresas |

Rotas marcadas como protegidas exigem sessão quando `APP_PASSWORD` está
definida; caso contrário, a aplicação fica aberta.

---

## Perfis de triagem

| Chave | Critério |
|---|---|
| `dividendario` | DY ≥ 5%, P/L ≤ 15, ROE ≥ 8%, liq. corrente ≥ 0,8, dívida/PL ≤ 1,0, liquidez ≥ R$ 500 mil/dia |
| `valor` | P/L ≤ 10, P/VP ≤ 1,5, EV/EBITDA ≤ 8, dívida/PL ≤ 0,8, liq. corrente ≥ 0,8 |
| `qualidade` | ROE ≥ 12%, ROIC ≥ 8%, margem EBIT ≥ 8%, dívida/PL ≤ 1,0, liq. corrente ≥ 0,8 |
| `graham` | Os 7 critérios defensivos de Graham + preço abaixo do Graham Number |
| `bazin` | DY ≥ 6%, P/L ≤ 15, ROE ≥ 15%, liq. corrente ≥ 1,0, dívida/PL ≤ 0,7 |
| `conservador` | Dívida/PL ≤ 0,5, liq. corrente ≥ 1,2, ROE ≥ 5%, DY ≥ 3%, liquidez ≥ R$ 1 mi/dia |
| `crescimento` | Crescimento da receita 5a ≥ 5%, margem EBIT ≥ 6% |

---

## Estrutura

```
Aura/
├── api/index.py           # FastAPI (função serverless da Vercel)
├── assets/                # frontend estático (sem build)
│   ├── styles.css
│   └── app.js
├── index.html             # interface
├── src/
│   ├── analise.py         # orquestrador: triagem → contexto → parecer
│   ├── config.py          # configuração via .env
│   ├── perfil.py          # questionário do investidor + instruções de estilo
│   ├── util.py            # parsing e formatação pt-BR
│   ├── dados/
│   │   ├── fundamentus.py  # fundamentalistas da B3
│   │   ├── yahoo.py       # preços, proventos, performance
│   │   └── cache.py       # cache em disco com TTL
│   ├── ia/
│   │   ├── gemini.py      # cliente Gemini com fallback e retry
│   │   └── prompt.py      # prompt e schema de saída
│   └── screen/
│       └── criterios.py   # perfis de triagem e pontuação
├── run.py                 # CLI
├── vercel.json
└── requirements.txt
```

---

## Limitações conhecidas

- **A cota free do Gemini é por modelo** (requisições por minuto e por dia). Quando um
  modelo estoura a cota, o Aura cai automaticamente para o próximo da cadeia — mas se
  todos estourarem ao mesmo tempo, a chamada falha com 429. A cota da sua conta fica em
  <https://aistudio.google.com/app/apikey>. Em uso normal, handful de análises por hora
  cabe folgado.
- O Fundamentus não publica API: o scraper depende do HTML e quebra se o site mudar.
  Há cache de 12h e um fallback para o último dado conhecido quando a fonte falha, mas
  uma mudança de layout exige ajuste em `src/dados/fundamentus.py`.
- O yfinance não cobre todos os papéis (ex.: `BRFS3.SA` não existe). Onde falta dado
  do yfinance, o parecer sai sem preço/proventos — e a IA é instruída a declarar "n/d".
- O dividend yield do Fundamentus é calculado sobre a cotação do dia e pode divergir
  do provento efetivamente pago (o Aura cruza com a série real de dividendos).
- **FIIs e ações não se misturam.** O Fundamentus mantém os fundos (383) em tabela
  separada, com métricas próprias. Na triagem, um FII passa só pelos filtros que
  fazem sentido para fundo (dividend yield, P/VP, liquidez) — P/L, ROE e EV/EBITDA
  de fundo não são comparáveis aos de uma companhia aberta.
- **Cuidado com o campo "Tipo" do Fundamentus**: ele é a *classe* da ação
  (ON, PN, ON NM, PN N2…), não um marcador de fundo. "ON NM" aparece em VALE3,
  LOGG3, CURY3 e LPSB3 — que são empresas. Só a lista de fundos do Fundamentus
  diz o que é FII.
- A triagem enriquece os 45 primeiros colocados com nome, classe e setor; o resto da
  lista aparece só com os indicadores, porque cada nome exige uma requisição ao
  Fundamentus.
- **Ferramenta de estudo.** Não é recomendação de investimento.

---

## Licença

MIT
