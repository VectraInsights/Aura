/* Aura — lógica da interface (vanilla JS, sem build). */
"use strict";

const state = {
  perfisTriagem: [],
  perfisAnalise: [],
  colunas: [],
  resultados: [],
  selecionados: new Set(),
  ordenacao: { chave: null, asc: true },
  ultimoPerfil: null,
  onboarding: null,
  classeFiltro: "",
};

const $ = (id) => document.getElementById(id);
const el = (tag, cls, texto) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (texto !== undefined) n.textContent = texto;
  return n;
};

// ------------------------------------------------------------------ formatação
const fmtNum = (v, casas = 2) => {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v.toLocaleString("pt-BR", { minimumFractionDigits: casas, maximumFractionDigits: casas });
};
const fmtPct = (v) => (v === null || v === undefined || Number.isNaN(v) ? "—" : `${fmtNum(v, 1)}%`);
const fmtInt = (v) => {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const abs = Math.abs(v);
  if (abs >= 1e12) return `${(v / 1e12).toLocaleString("pt-BR", { maximumFractionDigits: 2 })} tri`;
  if (abs >= 1e9) return `${(v / 1e9).toLocaleString("pt-BR", { maximumFractionDigits: 2 })} bi`;
  if (abs >= 1e6) return `${(v / 1e6).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} mi`;
  return v.toLocaleString("pt-BR", { maximumFractionDigits: 0 });
};

// ------------------------------------------------------------------ API
// O prefixo da API é descoberto em tempo de execução: a Vercel pode
// entregar as rotas reescritas (/api/index.py/...) e um proxy pode
// remover o prefixo. Descobrir o que responde evita tela quebrada.
//
// Toda chamada leva ?rota=<caminho>: a query string sobrevive ao rewrite
// da Vercel (que descarta o caminho original), então é ela que garante
// que a API receba a rota certa.
let BASE_API = "/api";

function urlDaApi(caminho) {
  const partes = String(caminho).split("?");
  const rota = partes[0];
  const params = new URLSearchParams(partes[1] || "");
  params.set("rota", rota);
  return `${BASE_API}?${params.toString()}`;
}

async function descobrirBase() {
  const candidatos = ["/api", "", "/api/index.py"];
  for (const base of candidatos) {
    try {
      const anterior = BASE_API;
      BASE_API = base;
      const resposta = await fetch(urlDaApi("/health"), { headers: { Accept: "application/json" } });
      const texto = await resposta.text();
      let corpo = null;
      try { corpo = JSON.parse(texto); } catch (_) { corpo = null; }
      // A API responde JSON. A protecao da Vercel devolveria HTML de login,
      // que nao parseia — assim o prefixo errado nunca e aceito.
      if (corpo && (corpo.status || corpo.versao || corpo.detail)) {
        return base;
      }
      BASE_API = anterior;
    } catch (_) { /* tenta o próximo */ }
  }
  return BASE_API;
}

async function api(caminho, opcoes) {
  const controller = new AbortController();
  const limite = opcoes && opcoes.timeoutMs ? opcoes.timeoutMs : 45000;
  const timer = setTimeout(() => controller.abort(), limite);
  const config = { ...(opcoes || {}), signal: controller.signal };
  delete config.timeoutMs;

  try {
    const resposta = await fetch(urlDaApi(caminho), config);
    const texto = await resposta.text();
    let corpo = null;
    try { corpo = texto ? JSON.parse(texto) : null; } catch (_) { corpo = null; }

    if (!resposta.ok) {
      // Mostra o que veio de verdade: status + trecho do corpo. Sem isso,
      // um 401 (protecao da Vercel) e um 404 (rota) ficam indistinguiveis.
      const detalhe = (corpo && (corpo.detail || (corpo.error && corpo.error.message))) || null;
      const trecho = texto ? texto.slice(0, 160).replace(/\s+/g, " ") : "";
      let mensagem = detalhe ? `${detalhe} (HTTP ${resposta.status})` : `HTTP ${resposta.status}`;
      if (!detalhe && trecho) mensagem += ` — ${trecho}`;
      throw new Error(mensagem);
    }
    return corpo;
  } catch (e) {
    if (e.name === "AbortError") {
      throw new Error(`A requisição excedeu ${Math.round(limite / 1000)}s e foi cancelada. A fonte de dados pode estar lenta — tente novamente em instantes.`);
    }
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

// ------------------------------------------------------------------ estado da UI
function mostrarLoader(texto) {
  $("loader-text").textContent = texto;
  $("loader").hidden = false;
  $("error-box").hidden = true;
}
function esconderLoader() { $("loader").hidden = true; }

function mostrarErro(msg) {
  $("error-box").hidden = false;
  $("error-text").textContent = msg;
  $("empty").hidden = true;
}

function setStatus(texto, classe) {
  const s = $("status");
  s.textContent = texto;
  s.className = `status ${classe || ""}`;
}

// ------------------------------------------------------------------ login / onboarding
async function verificarSessao() {
  let dados;
  try {
    dados = await api("/sessao");
  } catch (e) {
    return null;
  }
  if (!dados.autenticado) {
    $("login-overlay").hidden = false;
    $("btn-logout").hidden = true;
    $("btn-perfil").hidden = true;
    return dados;
  }
  $("login-overlay").hidden = true;
  $("btn-logout").hidden = !dados.login_ativo;
  $("btn-perfil").hidden = false;
  if (!dados.perfil_preenchido) {
    await abrirOnboarding();
  }
  return dados;
}

async function fazerLogin(evento) {
  evento.preventDefault();
  const erro = $("login-erro");
  erro.hidden = true;
  const senha = $("login-senha").value;
  try {
    await api("/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ senha }),
      timeoutMs: 15000,
    });
    $("login-senha").value = "";
    await verificarSessao();
    await init();
  } catch (e) {
    erro.textContent = e.message;
    erro.hidden = false;
  }
}

async function sair() {
  try {
    await api("/logout", { method: "POST", timeoutMs: 10000 });
  } catch (_) { /* segue o logout local */ }
  location.reload();
}

// ------------------------------------------------------------------ radar
async function gerarRadar() {
  const btn = $("btn-radar");
  btn.disabled = true;
  btn.querySelector(".btn-label").textContent = "Analisando…";
  $("radar-overlay").hidden = false;
  $("radar-corpo").innerHTML =
    `<div class="loader"><div class="spinner"></div><p>A IA está reordenando os candidatos…</p></div>`;

  try {
    const dados = await api(
      `/radar?perfil=${encodeURIComponent($("perfil").value)}&limite=12&incluir_fiis=true`,
      { method: "POST", timeoutMs: 180000 }
    );
    renderRadar(dados);
  } catch (e) {
    $("radar-corpo").innerHTML = `<div class="error-box"><strong>Falha ao gerar o radar.</strong> ${escapar(e.message)}</div>`;
  } finally {
    btn.disabled = false;
    btn.querySelector(".btn-label").textContent = "Gerar radar";
  }
}

function renderRadar(dados) {
  const radar = dados.radar || {};
  const meta = dados.meta || {};
  const corpo = $("radar-corpo");
  corpo.innerHTML = "";

  $("radar-titulo").textContent = "O que a IA recomenda agora";
  $("radar-barra").style.width = "100%";

  if (radar.resumo) {
    const resumo = el("div", "radar-resumo");
    resumo.textContent = radar.resumo;
    corpo.appendChild(resumo);
  }

  if ((radar.alertas || []).length) {
    const caixa = el("div", "radar-alertas");
    caixa.appendChild(el("h3", null, "Alertas"));
    const ul = el("ul");
    (radar.alertas || []).forEach((aviso) => ul.appendChild(el("li", null, aviso)));
    caixa.appendChild(ul);
    corpo.appendChild(caixa);
  }

  (radar.por_classe || []).forEach((grupo) => {
    const bloco = el("div", "radar-classe");
    bloco.appendChild(el("h3", null, grupo.classe));

    (grupo.destaques || []).forEach((d, i) => {
      const item = el("div", "radar-item");
      item.appendChild(el("div", `radar-pos${i === 0 ? " top" : ""}`, String(d.ranking ?? i + 1)));

      const conteudo = el("div", "radar-corpo");
      const topo = el("div", "radar-topo");
      topo.appendChild(el("span", "radar-ticker", d.ticker));
      if (d.nota !== undefined && d.nota !== null) {
        topo.appendChild(el("span", "radar-nota", `${d.nota}/10`));
      }
      conteudo.appendChild(topo);

      // Preço atual e variação do dia
      const precos = dados.precos || {};
      const cot = precos[d.ticker] || {};
      if (cot.preco != null) {
        const v = cot.variacao_dia != null ? cot.variacao_dia : 0;
        const cls = v >= 0 ? "pos" : "neg";
        const sinal = v >= 0 ? "+" : "";
        const precoEl = el("div", "radar-preco", `R$ ${cot.preco.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);
        const varEl = el("div", `radar-variacao ${cls}`, `${sinal}${v.toFixed(2)}%`);
        const linha = el("div", "radar-linha-preco");
        linha.appendChild(precoEl);
        linha.appendChild(varEl);
        conteudo.appendChild(linha);
      }

      if (d.veredito) conteudo.appendChild(el("div", "radar-veredito", d.veredito));
      if (d.motivo) conteudo.appendChild(el("div", "radar-motivo", d.motivo));
      if (d.cuidados) conteudo.appendChild(el("div", "radar-cuidado", `⚠ ${d.cuidados}`));
      item.appendChild(conteudo);
      bloco.appendChild(item);
    });

    (grupo.evitar || []).forEach((ev) => {
      const item = el("div", "radar-item radar-evitar");
      item.appendChild(el("div", "radar-pos", "✕"));
      const conteudo = el("div", "radar-corpo");
      const topo = el("div", "radar-topo");
      topo.appendChild(el("span", "radar-ticker", ev.ticker));
      conteudo.appendChild(topo);

      // Preço atual e variação do dia (também para os evitados)
      const precos = dados.precos || {};
      const cot = precos[ev.ticker] || {};
      if (cot.preco != null) {
        const v = cot.variacao_dia != null ? cot.variacao_dia : 0;
        const cls = v >= 0 ? "pos" : "neg";
        const sinal = v >= 0 ? "+" : "";
        const precoEl = el("div", "radar-preco", `R$ ${cot.preco.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);
        const varEl = el("div", `radar-variacao ${cls}`, `${sinal}${v.toFixed(2)}%`);
        const linha = el("div", "radar-linha-preco");
        linha.appendChild(precoEl);
        linha.appendChild(varEl);
        conteudo.appendChild(linha);
      }

      if (ev.motivo) conteudo.appendChild(el("div", "radar-motivo", ev.motivo));
      item.appendChild(conteudo);
      bloco.appendChild(item);
    });

    (grupo.evitar || []).forEach((ev) => {
      const item = el("div", "radar-item radar-evitar");
      item.appendChild(el("div", "radar-pos", "✕"));
      const conteudo = el("div", "radar-corpo");
      const topo = el("div", "radar-topo");
      topo.appendChild(el("span", "radar-ticker", ev.ticker));
      conteudo.appendChild(topo);
      if (ev.motivo) conteudo.appendChild(el("div", "radar-motivo", ev.motivo));
      item.appendChild(conteudo);
      bloco.appendChild(item);
    });

    if (!(grupo.destaques || []).length && !(grupo.evitar || []).length) {
      const vazio = el("p", "hint", "Nenhum candidato desta classe passou no critério da IA.");
      bloco.appendChild(vazio);
    }
    corpo.appendChild(bloco);
  });

  // A base do julgamento, visível.
  const base = $("radar-criterios");
  base.innerHTML = "";
  const criterios = dados.criterios || {};
  Object.keys(criterios).forEach((classe) => {
    base.appendChild(el("h4", null, classe));
    base.appendChild(el("p", null, criterios[classe]));
  });
  if (dados.limites) {
    base.appendChild(el("h4", null, "Limites"));
    base.appendChild(el("p", null, dados.limites));
  }

  const candidatos = dados.candidatos || {};
  const total = Object.values(candidatos).reduce((s, l) => s + l.length, 0);
  $("radar-meta").textContent =
    `${total} candidatos em ${Object.keys(candidatos).length} classes · ` +
    `modelo ${meta.modelo} · ${meta.segundos}s · ` +
    `Leitura da IA sobre números públicos — não é ordem de compra.`;
}

// ------------------------------------------------------------------ filtro por classe
function filtrarPorClasse(classe) {
  state.classeFiltro = classe;
  document.querySelectorAll(".classe-tab").forEach((b) => {
    b.classList.toggle("ativa", b.dataset.classe === classe);
  });
  renderTabela();
}

// ------------------------------------------------------------------ monitor
async function carregarRegras() {
  try {
    const dados = await api("/regras");
    state.regras = dados.regras || [];
    state.cotacoes = dados.cotacoes || {};
    renderRegras();
  } catch (e) {
    const box = $("regra-erro");
    box.textContent = e.message;
    box.hidden = false;
  }
}

function renderRegras() {
  const lista = $("regra-lista");
  lista.innerHTML = "";
  const { regras = [], cotacoes = {} } = state;

  if (!regras.length) {
    lista.appendChild(el("div", "hint", "Nenhuma regra ainda. Crie a primeira acima."));
    return;
  }

  regras.forEach((regra) => {
    const cot = cotacoes[regra.ativo.toUpperCase()] || {};
    const item = el("div", `regra-item${regra.armada ? "" : " disparada"}`);

    const topo = el("div", "regra-topo");
    topo.appendChild(el("span", "regra-desc", `${regra.ativo} ${regra.operador} ${regra.valor}`));
    const direita = el("div", "regra-topo");
    direita.appendChild(el("span", "regra-preco",
      cot.preco != null ? cot.preco.toLocaleString("pt-BR", { maximumFractionDigits: 4 }) : "—"));
    const remover = el("button", "regra-remover", "✕");
    remover.addEventListener("click", () => removerRegra(regra.id));
    direita.appendChild(remover);
    topo.appendChild(direita);
    item.appendChild(topo);

    const estado = el("div", "regra-estado");
    estado.innerHTML =
      `<span class="${regra.armada ? "" : "gatilho"}">${regra.armada ? "aguardando" : "gatilho atingido"}</span>` +
      ` · ação: ${regra.acao}${regra.ativo_alvo ? " " + regra.ativo_alvo : ""}` +
      (cot.fonte ? ` · fonte: ${cot.fonte}` : "");
    item.appendChild(estado);

    lista.appendChild(item);
  });
}

async function removerRegra(id) {
  try {
    await api(`/regras/${id}`, { method: "DELETE", timeoutMs: 15000 });
    await carregarRegras();
  } catch (e) {
    const box = $("regra-erro");
    box.textContent = e.message;
    box.hidden = false;
  }
}

async function criarRegra(evento) {
  evento.preventDefault();
  const box = $("regra-erro");
  box.hidden = true;

  const operador = $("regra-operador").value;
  const corpo = {
    ativo: $("regra-ativo").value.trim(),
    operador,
    valor: Number($("regra-valor").value),
    acao: $("regra-acao").value,
    ativo_alvo: $("regra-alvo").value.trim() || null,
    nota: $("regra-nota").value.trim(),
  };
  if (operador === "entre") corpo.valor2 = Number($("regra-valor2").value);

  try {
    await api("/regras", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(corpo),
      timeoutMs: 20000,
    });
    $("regra-ativo").value = "";
    $("regra-valor").value = "";
    $("regra-valor2").value = "";
    $("regra-alvo").value = "";
    $("regra-nota").value = "";
    await carregarRegras();
  } catch (e) {
    box.textContent = e.message;
    box.hidden = false;
  }
}

async function verificarMonitor() {
  const btn = $("btn-verificar");
  btn.disabled = true;
  btn.textContent = "Verificando…";
  try {
    const r = await api("/monitor/verificar", { method: "POST", timeoutMs: 60000 });
    const n = (r.disparadas || []).length;
    $("canais-info").textContent = n
      ? `${n} alerta(s) disparado(s)`
      : `nenhum gatilho atingido · ${r.verificado_em.replace("T", " ")}`;
    await carregarRegras();
  } catch (e) {
    $("canais-info").textContent = e.message;
  } finally {
    btn.disabled = false;
    btn.textContent = "Verificar agora";
  }
}

async function carregarCanais() {
  try {
    const c = await api("/monitor/canais");
    const ativos = Object.entries(c)
      .filter(([k, v]) => v && k !== "historico_local")
      .map(([k]) => k);
    $("canais-info").textContent = ativos.length
      ? `canais: ${ativos.join(", ")}`
      : "sem canal configurado — só histórico local";
  } catch (_) { /* silencioso */ }
}

// ------------------------------------------------------------------ onboarding
// Assistente de telas: uma pergunta por vez, com progresso e navegacao.
const ETAPAS_ONBOARDING = [
  { chave: "experiencia", etapa: "Começando" },
  { chave: "tolerancia", etapa: "Seu risco" },
  { chave: "horizonte", etapa: "Seu prazo" },
  { chave: "objetivo", etapa: "Sua meta" },
  { chave: "estrategia", etapa: "Sua estratégia" },
  { chave: "universo", etapa: "Seu universo" },
  { chave: "tamanho", etapa: "Sua carteira" },
  { chave: "dy_minimo", etapa: "Seu provento" },
  { chave: "observacoes", etapa: "Sua voz" },
];

function etapaDe(campo) {
  const achada = ETAPAS_ONBOARDING.find((e) => e.chave === campo);
  return achada ? achada.etapa : "";
}

async function abrirOnboarding() {
  let dados;
  try {
    dados = await api("/perfil");
  } catch (e) {
    return;
  }
  state.onboarding = {
    questoes: dados.questoes || [],
    respostas: { ...(dados.respostas || {}) },
    indice: 0,
  };
  $("onboarding-erro").hidden = true;
  $("onboarding-overlay").hidden = false;
  renderizarOnboarding();
}

function renderizarOnboarding() {
  const ob = state.onboarding;
  if (!ob || !ob.questoes.length) return;

  const total = ob.questoes.length;
  const i = Math.min(ob.indice, total - 1);
  const pergunta = ob.questoes[i];
  ob.indice = i;

  // Progresso
  $("ob-progress").textContent = `${i + 1} de ${total}`;
  $("ob-barra").style.width = `${((i + 1) / total) * 100}%`;
  $("ob-etapa").textContent = etapaDe(pergunta.campo);
  $("ob-titulo").textContent = pergunta.titulo || "";
  $("ob-ajuda").textContent = pergunta.ajuda || "";
  $("ob-ajuda").hidden = !pergunta.ajuda;

  // Resposta
  const container = $("ob-resposta");
  container.innerHTML = "";
  const resposta = ob.respostas[pergunta.campo];

  if (pergunta.tipo === "numero" || pergunta.tipo === "texto") {
    const input = document.createElement("input");
    input.type = pergunta.tipo === "numero" ? "number" : "text";
    input.id = "ob-input";
    input.placeholder = pergunta.placeholder || "";
    if (pergunta.tipo === "numero") {
      input.step = "0.1";
      input.min = "0";
      input.value = resposta ?? pergunta.padrao ?? "";
    } else {
      input.value = resposta ?? "";
    }
    input.addEventListener("input", () => {
      ob.respostas[pergunta.campo] = input.value;
      atualizarBotaoAvancar();
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); avancarOnboarding(); }
    });
    container.appendChild(input);
    if (pergunta.sufixo) {
      const s = el("div", "hint", pergunta.sufixo);
      s.style.marginTop = "6px";
      container.appendChild(s);
    }
    setTimeout(() => input.focus(), 60);
  } else {
    const grupo = el("div", "opcoes");
    (pergunta.opcoes || []).forEach((opcao) => {
      const label = el("label", "opcao");
      const radio = document.createElement("input");
      radio.type = "radio";
      radio.name = "ob-opcao";
      radio.value = opcao.valor;
      if (resposta === opcao.valor) radio.checked = true;
      label.appendChild(radio);
      label.appendChild(el("span", null, opcao.rotulo));
      radio.addEventListener("change", () => {
        ob.respostas[pergunta.campo] = radio.value;
        atualizarBotaoAvancar();
        // Avanca sozinho em perguntas de escolha: o ritmo fica fluido.
        setTimeout(() => avancarOnboarding(), 260);
      });
      grupo.appendChild(label);
    });
    container.appendChild(grupo);
  }

  // Botoes
  $("ob-voltar").disabled = i === 0;
  const botao = $("ob-avancar");
  botao.textContent = i === total - 1 ? "Concluir" : "Continuar";
  atualizarBotaoAvancar();
}

function atualizarBotaoAvancar() {
  const ob = state.onboarding;
  if (!ob) return;
  const pergunta = ob.questoes[ob.indice];
  const valor = ob.respostas[pergunta.campo];
  const obrigatorio = pergunta.tipo !== "texto";
  const preenchido = obrigatorio ? String(valor ?? "").trim() !== "" : true;
  $("ob-avancar").disabled = !preenchido;
}

async function avancarOnboarding() {
  const ob = state.onboarding;
  if (!ob) return;
  const total = ob.questoes.length;

  if (ob.indice >= total - 1) {
    await salvarOnboarding();
    return;
  }
  ob.indice += 1;
  renderizarOnboarding();
}

function voltarOnboarding() {
  const ob = state.onboarding;
  if (!ob || ob.indice === 0) return;
  ob.indice -= 1;
  renderizarOnboarding();
}

async function salvarOnboarding() {
  const ob = state.onboarding;
  if (!ob) return;
  const erro = $("onboarding-erro");
  erro.hidden = true;

  // Converte o campo numerico antes de enviar.
  const payload = { ...ob.respostas };
  const pergunta = ob.questoes[ob.indice];
  if (pergunta && pergunta.tipo === "numero") {
    payload[pergunta.campo] = Number(payload[pergunta.campo] ?? pergunta.padrao ?? 0);
  }

  try {
    await api("/perfil", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      timeoutMs: 15000,
    });
    $("onboarding-overlay").hidden = true;
  } catch (e) {
    erro.textContent = e.message;
    erro.hidden = false;
  }
}

// ------------------------------------------------------------------ init
async function init() {
  // Descobre o prefixo da API antes de qualquer chamada.
  await descobrirBase();

  try {
    const saude = await api("/health");
    setStatus(saude.ia_configurada ? `IA pronta · ${saude.modelo_padrao}` : "IA não configurada",
      saude.ia_configurada ? "ok" : "fail");
  } catch (e) {
    setStatus("API offline", "fail");
  }

  try {
    const sessao = await verificarSessao();
    if (sessao && sessao.autenticado) {
      const perfis = await api("/perfis");
      state.perfisTriagem = perfis.triagem;
      state.perfisAnalise = perfis.analise;
      montarSelects();
      await carregarRegras();
      await carregarCanais();
    }
  } catch (e) {
    mostrarErro(`Não foi possível carregar os perfis: ${e.message}`);
  }
}

function montarSelects() {
  const selPerfil = $("perfil");
  selPerfil.innerHTML = "";
  state.perfisTriagem.forEach((p) => {
    const o = el("option", null, p.rotulo);
    o.value = p.chave;
    selPerfil.appendChild(o);
  });
  selPerfil.value = "qualidade";
  atualizarPerfilDescricao();

  const selIa = $("perfil-ia");
  selIa.innerHTML = "";
  state.perfisAnalise.forEach((p) => {
    const o = el("option", null, p.rotulo);
    o.value = p.chave;
    selIa.appendChild(o);
  });
  selIa.value = "qualidade";
  atualizarPerfilIaDescricao();
}

function atualizarPerfilDescricao() {
  const p = state.perfisTriagem.find((x) => x.chave === $("perfil").value);
  if (!p) return;
  $("perfil-desc").textContent = p.descricao;
  const cx = $("perfil-criterios");
  cx.innerHTML = "";
  (p.criterios || []).forEach((c) => cx.appendChild(el("span", null, c)));
  (p.extras || []).forEach((c) => cx.appendChild(el("span", null, c)));
}

function atualizarPerfilIaDescricao() {
  const p = state.perfisAnalise.find((x) => x.chave === $("perfil-ia").value);
  if (p) $("perfil-ia-desc").textContent = p.descricao;
}

// ------------------------------------------------------------------ triagem
async function executarTriagem() {
  const btn = $("btn-triagem");
  btn.disabled = true;
  mostrarLoader("Coletando a B3 inteira e aplicando os filtros…");

  try {
    const perfil = $("perfil").value;
    const limite = $("limite").value;
    const fiis = $("incluir-fiis").checked;
    const dados = await api(`/triagem?perfil=${perfil}&limite=${limite}&incluir_fiis=${fiis}`, { timeoutMs: 60000 });

    state.colunas = dados.colunas;
    state.resultados = dados.resultados;
    state.selecionados.clear();
    state.ultimoPerfil = dados.perfil;

    $("results-title").textContent = `Triagem · ${dados.perfil.rotulo}`;
    $("results-summary").textContent =
      `${dados.total_aprovado} empresas aprovaram em ${dados.total_mercado} papéis da B3.`;
    $("results-head").hidden = false;
    $("empty").hidden = true;
    $("table-wrap").hidden = false;

    renderTabela();
    atualizarSelecionados();
    esconderLoader();
  } catch (e) {
    esconderLoader();
    mostrarErro(`Falha na triagem: ${e.message}`);
  } finally {
    btn.disabled = false;
  }
}

// ------------------------------------------------------------------ tabela
function colunasVisiveis() {
  const ordem = ["posicao", "ticker", "nome", "cotacao", "divyield", "pl", "pvp",
    "ev_ebitda", "roe", "roic", "liquidez_corrente", "div_liq_patrimonio",
    "crescimento_receita_5a", "margem_ebit", "eh_fii"];
  const disponiveis = state.colunas.map((c) => c.chave);
  return ordem.filter((c) => disponiveis.includes(c));
}

function formatarCelula(chave, valor) {
  if (valor === null || valor === undefined || valor === "") return "—";
  switch (chave) {
    case "cotacao": return `R$ ${fmtNum(valor)}`;
    case "divyield": case "roe": case "roic": case "margem_ebit":
    case "margem_bruta": case "margem_liquida": case "crescimento_receita_5a":
      return fmtPct(valor);
    case "liquidez_diaria": case "patrimonio_liquido": return fmtInt(valor);
    case "posicao": return String(valor);
    case "eh_fii": return valor ? "FII" : "";
    default: return typeof valor === "number" ? fmtNum(valor) : String(valor);
  }
}

function classeCelula(chave, valor) {
  if (valor === null || valor === undefined) return "dim";
  if (chave === "divyield") return valor >= 8 ? "pos" : "";
  if (chave === "roe" || chave === "roic") return valor >= 15 ? "pos" : "";
  if (chave === "pl") return valor > 0 && valor < 10 ? "pos" : "";
  if (chave === "div_liq_patrimonio") return valor > 1 ? "neg" : "";
  return "";
}

function renderTabela() {
  const visiveis = colunasVisiveis();
  const rotulos = Object.fromEntries(state.colunas.map((c) => [c.chave, c.rotulo]));
  const thead = $("tabela").querySelector("thead");
  const tbody = $("tabela").querySelector("tbody");
  thead.innerHTML = "";
  tbody.innerHTML = "";

  // cabecalho
  const trh = el("tr");
  const thSel = el("th", "left", "");
  thSel.style.cursor = "default";
  trh.appendChild(thSel);
  visiveis.forEach((chave) => {
    const th = el("th", chave === "ticker" || chave === "nome" ? "left" : "", rotulos[chave] || chave);
    th.dataset.chave = chave;
    if (state.ordenacao.chave === chave) {
      th.classList.add("sorted");
      if (state.ordenacao.asc) th.classList.add("asc");
    }
    th.addEventListener("click", () => ordenarPor(chave));
    trh.appendChild(th);
  });
  const thAcoes = el("th", "", "");
  thAcoes.style.cursor = "default";
  trh.appendChild(thAcoes);
  thead.appendChild(trh);

  // corpo
  const termo = ($("busca").value || "").trim().toLowerCase();
  let linhas = state.resultados;
  if (state.classeFiltro) {
    linhas = linhas.filter((r) => (r.tipo || "") === state.classeFiltro);
  }
  if (termo) {
    linhas = linhas.filter((r) =>
      String(r.ticker || "").toLowerCase().includes(termo) ||
      String(r.nome || "").toLowerCase().includes(termo));
  }

  linhas.forEach((r) => {
    const tr = el("tr");
    if (state.selecionados.has(r.ticker)) tr.classList.add("sel");

    const tdSel = el("td", "left");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = state.selecionados.has(r.ticker);
    cb.addEventListener("change", () => {
      if (cb.checked) state.selecionados.add(r.ticker);
      else state.selecionados.delete(r.ticker);
      tr.classList.toggle("sel", cb.checked);
      atualizarSelecionados();
    });
    tdSel.appendChild(cb);
    tr.appendChild(tdSel);

    visiveis.forEach((chave) => {
      const td = el("td", "");
      const valor = r[chave];
      if (chave === "ticker") {
        td.className = "left cell-ticker";
        td.appendChild(document.createTextNode(String(valor ?? "—")));
        if (r.eh_fii) td.appendChild(el("span", "tag", "FII"));
      } else if (chave === "nome") {
        td.className = "left cell-nome";
        td.textContent = valor || "—";
        td.title = valor || "";
      } else {
        td.textContent = formatarCelula(chave, valor);
        const c = classeCelula(chave, valor);
        if (c) td.className = c;
      }
      tr.appendChild(td);
    });

    const tdAcoes = el("td");
    const acoes = el("div", "row-actions");
    const b1 = el("button", "btn tiny", "Analisar");
    b1.addEventListener("click", () => analisar(r.ticker));
    acoes.appendChild(b1);
    tdAcoes.appendChild(acoes);
    tr.appendChild(tdAcoes);

    tbody.appendChild(tr);
  });

  if (!linhas.length) {
    const tr = el("tr");
    const td = el("td", "dim", "Nenhuma empresa encontrada com esse filtro.");
    td.colSpan = visiveis.length + 2;
    tr.appendChild(td);
    tbody.appendChild(tr);
  }
}

function ordenarPor(chave) {
  if (state.ordenacao.chave === chave) {
    state.ordenacao.asc = !state.ordenacao.asc;
  } else {
    state.ordenacao.chave = chave;
    state.ordenacao.asc = true;
  }
  const { chave: c, asc } = state.ordenacao;
  state.resultados.sort((a, b) => {
    const va = a[c], vb = b[c];
    if (va === null || va === undefined) return 1;
    if (vb === null || vb === undefined) return -1;
    if (typeof va === "number" && typeof vb === "number") return asc ? va - vb : vb - va;
    return asc ? String(va).localeCompare(String(vb)) : String(vb).localeCompare(String(va));
  });
  renderTabela();
}

function atualizarSelecionados() {
  $("sel-count").textContent = String(state.selecionados.size);
  $("btn-lote").disabled = state.selecionados.size === 0;
}

// ------------------------------------------------------------------ análise
function abrirDrawer() {
  $("drawer").hidden = false;
  $("drawer-backdrop").hidden = false;
}
function fecharDrawer() {
  $("drawer").hidden = true;
  $("drawer-backdrop").hidden = true;
}

async function analisar(ticker, perfil) {
  const per = perfil || $("perfil-ia").value;
  abrirDrawer();
  $("drawer-ticker").textContent = ticker;
  $("drawer-nome").textContent = "gerando parecer…";
  $("drawer-body").innerHTML = `<div class="loader"><div class="spinner"></div><p>Analisando ${ticker}…</p></div>`;

  try {
    const dados = await api(`/analise/${encodeURIComponent(ticker)}?perfil=${per}`, { timeoutMs: 90000 });
    renderParecer(ticker, dados);
  } catch (e) {
    $("drawer-nome").textContent = "";
    $("drawer-body").innerHTML = `<div class="error-box"><strong>Falha na análise.</strong> ${escapar(e.message)}</div>`;
  }
}

async function analisarLote() {
  const tickers = [...state.selecionados];
  if (!tickers.length) return;
  abrirDrawer();
  $("drawer-ticker").textContent = `Análise em lote (${tickers.length})`;
  $("drawer-nome").textContent = "";
  $("drawer-body").innerHTML = `<div class="loader"><div class="spinner"></div><p>Processando ${tickers.join(", ")}…</p></div>`;

  try {
    const dados = await api("/analise-lote", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tickers, perfil: $("perfil-ia").value }),
      timeoutMs: 120000,
    });
    $("drawer-body").innerHTML = "";
    (dados.resultados || []).forEach((r) => {
      if (r.ok) {
        renderParecer(r.ticker, r, true);
      } else {
        const box = el("div", "error-box");
        box.innerHTML = `<strong>${escapar(r.ticker)}</strong> — ${escapar(r.erro || "erro")}`;
        $("drawer-body").appendChild(box);
      }
    });
  } catch (e) {
    $("drawer-body").innerHTML = `<div class="error-box"><strong>Falha no lote.</strong> ${escapar(e.message)}</div>`;
  }
}

function escapar(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function secao(titulo, corpo, classe) {
  const s = el("div", "sec");
  s.appendChild(el("h3", null, titulo));
  if (Array.isArray(corpo)) {
    const ul = el("ul", classe || "");
    corpo.forEach((i) => ul.appendChild(el("li", null, i)));
    s.appendChild(ul);
  } else {
    const p = el("p");
    p.innerHTML = escapar(corpo).replace(/\n/g, "<br>");
    s.appendChild(p);
  }
  return s;
}

function renderParecer(ticker, dados, compacto) {
  const r = dados.relatorio || {};
  const meta = dados.meta || {};
  const ctx = dados.contexto || {};

  if (!compacto) {
    // Limpa o loader antes de montar o relatório.
    $("drawer-body").innerHTML = "";
    $("drawer-ticker").textContent = `${r.ticker || ticker}`;
    $("drawer-nome").textContent = ctx.nome || "";
  } else {
    const h = el("div");
    h.style.marginBottom = "26px";
    h.style.borderBottom = "1px solid var(--line)";
    h.style.paddingBottom = "10px";
    h.innerHTML = `<h3 style="font-size:16px">${escapar(r.ticker || ticker)} <span class="hint">${escapar(ctx.nome || "")}</span></h3>`;
    $("drawer-body").appendChild(h);
  }

  const alvo = $("drawer-body");
  const frag = document.createDocumentFragment();

  // veredito
  const v = el("div", "verdict");
  const score = el("div", "score");
  score.innerHTML = `${r.nota ?? "—"}/10<small>NOTA</small>`;
  v.appendChild(score);
  const vt = el("div");
  vt.appendChild(el("p", null, r.veredito || "—"));
  vt.appendChild(el("div", "conf",
    `Confiança: ${r.confianca || "—"} · ${ctx.tipo || "Ação"} · ${ctx.setor || "setor não informado"}`));
  v.appendChild(vt);
  frag.appendChild(v);

  if (r.sobre_empresa) frag.appendChild(secao("Sobre a empresa", r.sobre_empresa));
  if (r.resumo) frag.appendChild(secao("Resumo técnico", r.resumo));

  const col1 = el("div", "two-col");
  col1.appendChild(secao("Endividamento", r.endividamento || "n/d"));
  col1.appendChild(secao("Geração de caixa", r.geracao_caixa || "n/d"));
  frag.appendChild(col1);

  const col2 = el("div", "two-col");
  col2.appendChild(secao("Qualidade dos lucros", r.qualidade_lucros || "n/d"));
  col2.appendChild(secao("Veredito", r.veredito || "n/d"));
  frag.appendChild(col2);

  if (r.riscos && r.riscos.length) frag.appendChild(secao("Riscos", r.riscos, "risk"));
  if (r.catalisadores && r.catalisadores.length) frag.appendChild(secao("Catalisadores", r.catalisadores, "catal"));
  if (r.o_que_monitorar && r.o_que_monitorar.length) frag.appendChild(secao("O que monitorar", r.o_que_monitorar));
  if (r.alertas && r.alertas.length) frag.appendChild(secao("Alertas e lacunas de dados", r.alertas, "alert"));

  // números de referência
  const nums = el("div", "sec");
  nums.appendChild(el("h3", null, "Números de referência"));
  const ul = el("ul");
  const bal = ctx.balanco || {};
  const ind = ctx.indicadores || {};
  const linhas = [
    ["Cotação", ctx.cotacao != null ? `R$ ${fmtNum(ctx.cotacao)}` : "n/d"],
    ["Valor de mercado", fmtInt(ctx.valor_mercado)],
    ["Balanço", ctx.ultimo_balanco || "n/d"],
    ["Dívida líquida", fmtInt(bal.div_liquida)],
    ["Dívida bruta", fmtInt(bal.div_bruta)],
    ["Caixa", fmtInt(bal.disponibilidades)],
    ["Patrimônio líquido", fmtInt(bal.patrimonio_liquido)],
    ["Receita 12m", fmtInt((ctx.dre_anual || {}).receita_liquida)],
    ["EBIT 12m", fmtInt((ctx.dre_anual || {}).ebit)],
    ["Lucro líquido 12m", fmtInt((ctx.dre_anual || {}).lucro_liquido)],
    ["P/L", ind.pl != null ? fmtNum(ind.pl) : "n/d"],
    ["P/VP", ind.pvp != null ? fmtNum(ind.pvp) : "n/d"],
    ["ROE", ind.roe != null ? fmtPct(ind.roe) : "n/d"],
    ["Dividend yield", ind.divyield != null ? fmtPct(ind.divyield) : "n/d"],
  ];
  linhas.forEach(([k, v2]) => {
    const li = el("li");
    li.innerHTML = `<b>${escapar(k)}:</b> ${escapar(v2)}`;
    ul.appendChild(li);
  });
  nums.appendChild(ul);
  frag.appendChild(nums);

  const m = el("div", "meta");
  m.innerHTML = [
    `modelo <b>${escapar(meta.modelo || "—")}</b>`,
    `tokens ${meta.tokens_entrada ?? 0}→${meta.tokens_saida ?? 0}`,
    `${meta.segundos ?? "—"}s`,
    `foco ${escapar(dados.meta && dados.meta.perfil ? dados.meta.perfil : (state.ultimoPerfil && state.ultimoPerfil.chave) || "—")}`,
  ].join(" · ");
  frag.appendChild(m);

  alvo.appendChild(frag);
  if (!compacto) alvo.scrollTop = 0;
}

// ------------------------------------------------------------------ eventos
document.addEventListener("DOMContentLoaded", () => {
  $("perfil").addEventListener("change", atualizarPerfilDescricao);
  $("perfil-ia").addEventListener("change", atualizarPerfilIaDescricao);
  $("limite").addEventListener("input", (e) => { $("limite-out").textContent = e.target.value; });
  $("btn-triagem").addEventListener("click", executarTriagem);
  $("btn-lote").addEventListener("click", analisarLote);
  $("busca").addEventListener("input", renderTabela);
  $("drawer-close").addEventListener("click", fecharDrawer);
  $("drawer-backdrop").addEventListener("click", fecharDrawer);
  $("btn-docs").addEventListener("click", () => window.open("/docs", "_blank"));
  $("login-form").addEventListener("submit", fazerLogin);
  $("ob-avancar").addEventListener("click", avancarOnboarding);
  $("ob-voltar").addEventListener("click", voltarOnboarding);
  $("ob-pular").addEventListener("click", () => { $("onboarding-overlay").hidden = true; });
  $("btn-logout").addEventListener("click", sair);
  $("btn-perfil").addEventListener("click", abrirOnboarding);
  $("regra-form").addEventListener("submit", criarRegra);
  $("btn-verificar").addEventListener("click", verificarMonitor);
  $("btn-radar").addEventListener("click", gerarRadar);
  $("radar-close").addEventListener("click", () => { $("radar-overlay").hidden = true; });
  document.querySelectorAll(".classe-tab").forEach((aba) => {
    aba.addEventListener("click", () => filtrarPorClasse(aba.dataset.classe));
  });
  $("regra-operador").addEventListener("change", (e) => {
    $("regra-valor2").classList.toggle("oculto", e.target.value !== "entre");
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") fecharDrawer(); });
  init();
});
