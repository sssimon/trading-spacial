/* global React, window */
// ============================================================
// Valles · SP3 — IdeaView (idea-de-moneda) + RegimeFrame.
// Pieza 1: el régimen como MARCO envolvente y persistente.
// Pieza 3: jerarquía + microcopy con la costura que respira.
// Texto entre /*VERBATIM*/ no se toca (costura, AC7, frase régimen).
// <Prop> marca propuestas de microcopy (doctrina §4 respetada).
// ============================================================

const nm = window.sp3nm, $ = window.sp3$, pct1 = window.sp3pct1, edad = window.sp3edad;
const CH_LABEL = window.CH_LABEL, RAZONES_MUERTE = window.RAZONES_MUERTE;
const Chart = window.IdeaChart;

function Prop({ children }) { return <span className="prop" title="Propuesta de microcopy (no verbatim)">◆ prop · {children}</span>; }

// ── frescura átomo (fresco / rancio / muerto se ven distinto) ─
function Fresh({ frescura, noun }) {
  if (!frescura) return null;
  const e = frescura.estado;
  const cls = e === 'muerto' ? 'fr--muerto' : e === 'rancio' ? 'fr--rancio' : 'fr--fresco';
  let txt;
  if (e === 'muerto') txt = `sin ${noun || 'foto'} — el screener aún no ha completado un ciclo`;
  else if (e === 'rancio') txt = `${noun || 'foto'} ${edad(frescura.edad_seg)} · rancia`;
  else txt = `${noun || 'foto'} ${edad(frescura.edad_seg)}`;
  return <span className={`fr ${cls}`}><span className="fr__dot" />{txt}</span>;
}

// ════════════════════════════════════════════════════════════
// PIEZA 1 · MARCO DE RÉGIMEN
// ════════════════════════════════════════════════════════════
const LEAN_TXT = {
  alts: <>Inclinación del mercado: <b>hacia las alts</b></>,
  mixto: <>Inclinación del mercado: <b>mixta</b></>,
  btc: <>Inclinación del mercado: <b>hacia BTC</b></>,
};
const LEAN_SHORT = { alts: 'hacia alts', mixto: 'mixta', btc: 'hacia BTC' };
const LEAN_BARE = { alts: 'alts', mixto: 'lo mixto', btc: 'BTC' };

function RegimeFresh({ frescura }) {
  if (!frescura) return null;
  const e = frescura.estado;
  const cls = e === 'muerto' ? 'fr--muerto' : e === 'rancio' ? 'fr--rancio' : 'fr--fresco';
  const tail = e === 'muerto' ? 'muerto' : e === 'rancio' ? `rancio · ${edad(frescura.edad_seg)}` : `fresco · ${edad(frescura.edad_seg)}`;
  return <span className={`fr ${cls}`}><span className="fr__dot" />foto del régimen: {tail}</span>;
}

function Component({ k, label, render, comp }) {
  if (!comp || comp.estado === 'muerto') {
    return (
      <div className="rf__comp rf__comp--muerto">
        <div className="rf__comp-k">{label}</div>
        <div className="rf__comp-v">sin dato</div>
        <div className="rf__comp-lean">{comp && comp.razon ? `fuente caída` : 'fuente caída'}</div>
      </div>
    );
  }
  return (
    <div className="rf__comp">
      <div className="rf__comp-k">{label}</div>
      <div className="rf__comp-v">{render(comp.valor)}</div>
      <div className="rf__comp-lean">se inclina a {comp.lean === 'neutral' ? 'ninguno (neutral)' : comp.lean}</div>
    </div>
  );
}

function RegimeFrame({ regime }) {
  if (!regime || !regime.regime || regime.frescura.estado === 'muerto') {
    return (
      <header className="rf">
        <div className="rf__eyebrow">Clima del mercado</div>
        <div className="rf__lean">El régimen de mercado no está disponible ahora.</div>
        <div className="fr-dead" style={{ marginTop: 18 }}>
          <span className="fr-dead__icon">⧖</span>
          <div>
            <div className="fr-dead__t">La foto del régimen está caída</div>
            <div className="fr-dead__s">El productor del clima no respondió o aún no corrió un ciclo. No es un dato viejo disfrazado — es ausencia honesta.</div>
          </div>
        </div>
        <div className="rf__doctrine">{/*VERBATIM*/}Lo que más mueve el resultado es el régimen del mercado, no la moneda que elijas.</div>
      </header>
    );
  }
  const r = regime.regime;
  const votos = r.votos;
  const leanCount = votos[r.estado] || 0;
  return (
    <header className="rf">
      <div className="rf__eyebrow">Clima del mercado · enmarca esta moneda</div>
      <div className="rf__lean">{LEAN_TXT[r.estado]}</div>

      <div className="rf__components">
        <Component label="amplitud (alts sobre su media 50d)" comp={r.componentes.breadth50}
          render={(v) => (v * 100).toFixed(1) + '%'} />
        <Component label="alts vs BTC · 30 días" comp={r.componentes.outperf_30d}
          render={(v) => (v >= 0 ? '+' : '−') + Math.abs(v * 100).toFixed(1) + '%'} />
        <Component label="dominancia BTC" comp={r.componentes.dominancia_btc}
          render={(v) => (v * 100).toFixed(1) + '%'} />
      </div>

      <div style={{ marginTop: 12, fontSize: 14, color: 'var(--ink-3)' }}>
        Decidido por {votos.vivos} {votos.vivos === 1 ? 'juez' : 'jueces'} ·{' '}
        {leanCount} se {leanCount === 1 ? 'inclina' : 'inclinan'} a {LEAN_BARE[r.estado]}
        {votos.neutral ? `, ${votos.neutral} neutral` : ''}.
      </div>

      <p className="rf__doctrine">
        {/*VERBATIM*/}Lo que más mueve el resultado es <b>el régimen del mercado</b>, no la moneda que elijas.
      </p>
      <div className="rf__foot"><RegimeFresh frescura={regime.frescura} /></div>
    </header>
  );
}

// franja persistente (sticky) — repite el clima mientras se lee
function RegimeStrip({ regime }) {
  if (!regime || !regime.regime || regime.frescura.estado === 'muerto') {
    return (
      <div className="rf-strip">
        <span className="rf-strip__lean"><span className="rf-strip__chip">clima</span> no disponible</span>
        <span className="rf-strip__sep" />
        <Fresh frescura={regime ? regime.frescura : null} noun="foto" />
      </div>
    );
  }
  const r = regime.regime;
  return (
    <div className="rf-strip">
      <span className="rf-strip__lean"><span className="rf-strip__chip">clima</span> {LEAN_SHORT[r.estado]}</span>
      <span className="rf-strip__note">el clima manda sobre la moneda — no la valida</span>
      <span className="rf-strip__sep" />
      <RegimeFresh frescura={regime.frescura} />
    </div>
  );
}

// ════════════════════════════════════════════════════════════
// NARRATIVA — sub-bloques
// ════════════════════════════════════════════════════════════
function toquesDe(levels, centro) {
  const z = (levels.zonas || []).find((x) => Math.abs(x.centro - centro) < 1e-6);
  return z ? z.toques : null;
}

function VidaBlock({ evalData }) {
  if (!evalData || evalData.estado === 'no_disponible') {
    return (
      <div className="nb" id="idea-vida">
        <h3 className="nb__h">¿Está viva?</h3>
        <p className="nb__p">No se pudo revisar el estado de la moneda ahora. Puede ser un problema de la herramienta — intenta de nuevo en un momento.</p>
      </div>
    );
  }
  if (evalData.candidata === false) {
    return (
      <div className="nb" id="idea-vida">
        <h3 className="nb__h">¿Está viva?</h3>
        {evalData.vivo
          ? <p className="nb__p">No está en la parte baja de su rango ahora. Está viva, pero hoy cotiza en la parte alta de su rango de 30d (posición <b>{Math.round(evalData.pos_in_30d_range * 100)}%</b>), así que no entra en este filtro.</p>
          : <>
              <p className="nb__p">No está viva mecánicamente ahora. Esto fue lo que se vio:</p>
              <ul className="nb__list">{(evalData.razones_muerte || []).map((r) => <li className="nb__li" key={r}><span className="nb__li-b">—</span>{RAZONES_MUERTE[r] || r}</li>)}</ul>
            </>}
      </div>
    );
  }
  return (
    <div className="nb" id="idea-vida">
      <h3 className="nb__h">¿Está viva?</h3>
      <p className="nb__p">
        Está viva y en la <b>parte baja de su rango de 30d</b> (posición <b>{Math.round(evalData.pos_in_30d_range * 100)}%</b>),
        por debajo de su SMA20 (<b>{pct1(evalData.pct_vs_sma20)}</b>), RSI <b>{evalData.rsi14.toFixed(1)}</b>.
      </p>
      <div className="seam seam--ac7">
        {/*VERBATIM AC7*/}
        <p className="seam__txt">Esto es la réplica del filtro que usaba el canal de 2019. Medido, no le ganó al azar de alts ni en su mejor régimen (alt-bull 2019: 14d 9.92% vs 12.54%). Lo que movió el retorno fue el régimen, no esta selección. <b>La decisión es tuya.</b></p>
        <div className="seam__evi">
          <div className="seam__evi-item"><div className="seam__evi-v">9.92%</div><div className="seam__evi-k">recomendadas · 14 días</div></div>
          <div className="seam__evi-vs">vs</div>
          <div className="seam__evi-item"><div className="seam__evi-v seam__evi-v--azar">12.54%</div><div className="seam__evi-k">azar de alts · 14 días</div></div>
        </div>
      </div>
    </div>
  );
}

function ParedesBlock({ levels }) {
  if (!levels || levels.estado === 'no_disponible') {
    return (
      <div className="nb" id="idea-paredes">
        <h3 className="nb__h">¿Dónde está entre sus paredes?</h3>
        <p className="nb__p">No se pudieron calcular los niveles en este momento. Prueba de nuevo en un rato.</p>
      </div>
    );
  }
  const u = levels.ubicacion || {};
  const price = levels.price_live;
  if (!levels.zonas || levels.zonas.length === 0) {
    return (
      <div className="nb" id="idea-paredes">
        <h3 className="nb__h">¿Dónde está entre sus paredes?</h3>
        <p className="nb__p">Todavía no hay paredes claras: el precio no giró suficientes veces en ningún lugar como para marcar una pared.{price != null && <> Hoy vale <b>${$(price)}</b>.</>}</p>
      </div>
    );
  }
  let lead;
  if (u.dentro_de && u.dentro_de.tipo === 'soporte') lead = <>El precio está sobre un piso que ya giró <b>{u.dentro_de.toques} veces</b> — zona histórica de compradores.</>;
  else if (u.dentro_de && u.dentro_de.tipo === 'resistencia') lead = <>El precio está contra un techo que ya giró <b>{u.dentro_de.toques} veces</b> — zona donde el precio suele frenarse.</>;
  else lead = <>El precio está en el medio — no pegado a ninguna pared todavía.</>;
  const techoT = u.techo ? toquesDe(levels, u.techo.centro) : null;
  const pisoT = u.piso ? toquesDe(levels, u.piso.centro) : null;
  return (
    <div className="nb" id="idea-paredes">
      <h3 className="nb__h">¿Dónde está entre sus paredes?</h3>
      <p className="nb__p">{lead}{price != null && <> Precio actual: <b>${$(price)}</b>.</>}</p>
      <ul className="nb__list">
        {u.techo && <li className="nb__li"><span className="nb__li-b">▲</span>Techo más cercano: <b>${$(u.techo.centro)}</b>, queda {Math.abs(u.techo.dist_pct).toFixed(1)}% más arriba.{techoT && <> Ya rebotó {techoT} veces ahí.</>}</li>}
        {u.piso && <li className="nb__li"><span className="nb__li-b">▼</span>Piso más cercano: <b>${$(u.piso.centro)}</b>, queda {Math.abs(u.piso.dist_pct).toFixed(1)}% más abajo.{pisoT && <> Ya rebotó {pisoT} veces ahí.</>}</li>}
      </ul>
    </div>
  );
}

function JugadaBlock({ plan }) {
  const hasPlan = plan && plan.estado_vivo != null && plan.plan;
  return (
    <div className="nb" id="idea-jugada">
      <h3 className="nb__h">Si decides entrar, la jugada</h3>
      {!hasPlan
        ? <p className="nb__p">Todavía no hay un plan calculado para esta moneda. Puede que falten niveles o que la moneda no esté en condición de entrada ahora.</p>
        : (() => {
            const pl = plan.plan;
            const rungs = pl.rungs;
            const ladderTxt = rungs.length === 1
              ? <>Solo hay una pared clara arriba — una salida ({Math.round(rungs[0].size_frac * 100)}%) en <b>${$(rungs[0].tp_price)}</b>.</>
              : <>{rungs.length} peldaños ({rungs.map((r, i) => `${Math.round(r.size_frac * 100)}% a $${$(r.tp_price)}`).join(' / ')}). La primera salida es la más grande — sales más donde la pared está más cerca.</>;
            return (
              <ul className="nb__list">
                <li className="nb__li"><span className="nb__li-b">●</span>{pl.entry_zone ? <>Zona de entrada: zona <b>${$(pl.entry_zone.precio_bajo)}–${$(pl.entry_zone.precio_alto)}</b>, donde el precio ya rebotó {pl.entry_zone.toques} veces.</> : <>No se identificó una zona de soporte nítida.</>}</li>
                <li className="nb__li"><span className="nb__li-b">●</span>Stop: <b>${$(pl.sl_plan)}</b>{pl.sl_piso && <>, justo debajo del piso de ${$(pl.sl_piso.centro)}</>}. Es lo máximo que estás dispuesto a perder en esta jugada.</li>
                <li className="nb__li"><span className="nb__li-b">●</span>Escalera de salidas: {ladderTxt}</li>
                {pl.runner_frac > 0 && <li className="nb__li"><span className="nb__li-b">●</span>Runner: un <b>{Math.round(pl.runner_frac * 100)}%</b> queda abierto sin objetivo. Cuando se llena la primera salida, su stop sube a break-even — a partir de ahí esa parte ya no puede perder.</li>}
              </ul>
            );
          })()}
      <div className="seam">{/*VERBATIM*/}Esto sale de tus niveles · <b>la decisión es tuya.</b></div>
    </div>
  );
}

// ════════════════════════════════════════════════════════════
// ⑤ TU JUGADA AHORA — lifecycle
// ════════════════════════════════════════════════════════════
function PlayNow({ play }) {
  if (!play || play.estado_vivo == null) {
    return (
      <div className="play play--empty">
        <div className="play__lead">No hay plan calculado ahora mismo. Puedes revisar los bloques de arriba para ver el estado actual.</div>
      </div>
    );
  }
  if (play.estado_vivo === 'cerrado') {
    const ICON = { si: '✓', no: '○', dato: '·' };
    return (
      <div className="play">
        <div className="play__lead"><b>{play.titular}</b></div>
        <ul className="play__close-fields">
          {play.campos.map((c, i) => (
            <li className="play__cf" key={i}>
              <span className={`play__cf-i play__cf-i--${c.ok}`}>{ICON[c.ok]}</span>
              {c.k}{c.v && <span className="play__cf-v">{c.v}</span>}
            </li>
          ))}
        </ul>
      </div>
    );
  }
  const incierto = play.estado_vivo === 'incierto';
  const planListo = play.estado_vivo === 'activo' && play._fijada === false;
  const recienFijada = play._recienFijada;
  return (
    <div className={`play ${incierto ? 'play--incierto' : ''}`}>
      {recienFijada ? (
        <div className="play__status"><b>Jugada fijada</b> — se sigue en vivo. <Fresh frescura={play.frescura} noun="lectura" /></div>
      ) : planListo ? (
        <>
          <div className="play__lead">El plan está listo. Si decides entrar, fija la jugada y el sistema la sigue en vivo.</div>
          <button className="play__cta">Fijar esta jugada</button>
        </>
      ) : (
        <>
          <div className="play__status"><b>{incierto ? 'Jugada incierta' : 'Jugada en curso'}</b> · <Fresh frescura={play.frescura} noun="lectura" /></div>
          {incierto && <div className="play__lead" style={{ marginTop: 12 }}>El sistema no está seguro de dónde está la jugada — revisa en Binance.</div>}
          {play.hechos && play.hechos.length > 0 && (
            <ul className="play__facts">
              {play.hechos.map((h, i) => <li className="play__fact" key={i}><span className="play__fact-i play__fact-i--dato">·</span>{h}</li>)}
            </ul>
          )}
        </>
      )}
    </div>
  );
}

// ════════════════════════════════════════════════════════════
// ⑥ QUIÉN ESTÁ DETRÁS — dossier
// ════════════════════════════════════════════════════════════
function Dossier({ dossier, loading }) {
  if (loading) return <div className="iv-load"><span className="iv-load__spin" />Buscando quién está detrás…</div>;
  if (!dossier || dossier.estado_general === 'no_disponible') {
    return (
      <div className="iv-callout iv-callout--mute">
        <span className="iv-callout__icon">×</span>
        <div><div className="iv-callout__t">No se pudo averiguar ahora</div>
          <div className="iv-callout__s">Falló la búsqueda. Es un problema de la herramienta, no del proyecto.</div>
          <button className="dos__retry">↻ Intentar de nuevo</button></div>
      </div>
    );
  }
  if (dossier.estado_general === 'opaco') {
    return (
      <div className="dos">
        <div className="dos__lead-row">
          <div className="dos__icon dos__icon--opaco">◍</div>
          <div>
            <div className="dos__lead">No se encontró quién está detrás</div>
            <div className="dos__say">Se buscó equipo, presencia y actividad pública, y no apareció nada. Eso es un dato sobre el proyecto, no una falla de la herramienta.{dossier.no_encontrado_en && dossier.no_encontrado_en.length > 0 && <> No se halló en: {dossier.no_encontrado_en.join(', ')}.</>}</div>
          </div>
        </div>
      </div>
    );
  }
  const channels = Object.keys(dossier.presencia || {});
  return (
    <div className="dos">
      <div className="dos__lead-row">
        <div className="dos__icon">☻</div>
        <div>
          <div className="dos__lead">Se sabe quién está detrás</div>
          <div className="dos__say">Hay nombres y canales públicos, y cada dato se puede comprobar en su fuente.</div>
        </div>
      </div>
      <div className="dos__people">
        {dossier.equipo.map((m, i) => (
          <div className="dos__person" key={i}>
            <div className="dos__face">☻</div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="dos__name">{m.nombre}{m.rol && <span className="dos__role"> · {m.rol}</span>}</div>
            </div>
            {m.fuente && <a className="dos__src" href={m.fuente} target="_blank" rel="noreferrer">fuente</a>}
          </div>
        ))}
      </div>
      <div className="dos__channels">
        {channels.map((k) => {
          const p = dossier.presencia[k];
          const st = p.activo === 'si' ? 'activo' : p.activo === 'no' ? 'inactivo' : 'sin confirmar';
          return (
            <a className="dos__ch" key={k} href={p.url || '#'} target="_blank" rel="noreferrer">
              <span className={`dos__ch-dot dos__ch-dot--${p.activo}`} />
              {CH_LABEL[k] || k} <span className="dos__ch-state">· {st}</span>
              {p.fuente && <span className="dos__ch-src">· fuente</span>}
            </a>
          );
        })}
      </div>
    </div>
  );
}

// ════════════════════════════════════════════════════════════
// IDEA-VIEW — ensambla todo dentro del marco
// ════════════════════════════════════════════════════════════
const NAV = [
  { id: 'idea-vida', label: 'Vida' },
  { id: 'idea-paredes', label: 'Paredes' },
  { id: 'idea-jugada', label: 'Jugada' },
  { id: 'idea-quien', label: 'Quién' },
  { id: 'idea-noticias', label: 'Noticias' },
];

function IdeaView({ regime, evalData, levels, plan, play, dossier, dossierLoading, chartLayers, sym }) {
  const symbol = sym || (evalData && evalData.symbol) || (levels && levels.symbol) || 'INJUSDT';
  const price = (levels && levels.price_live) != null ? levels.price_live : (evalData && evalData.price);
  const evalUnavailable = !evalData || evalData.estado === 'no_disponible';
  return (
    <div className="iv">
      <div className="iv__frame">
        <RegimeFrame regime={regime} />
        <RegimeStrip regime={regime} />

        <div className="iv__body">
          <nav className="iv-nav" aria-label="Secciones de la moneda">
            {NAV.map((n, i) => (
              <React.Fragment key={n.id}>
                {i > 0 && <span className="iv-nav__sep" />}
                <a className={`iv-nav__a ${i === 0 ? 'iv-nav__a--on' : ''}`} href={`#${n.id}`}>{n.label}</a>
              </React.Fragment>
            ))}
          </nav>

          {/* ② cabecera de la moneda */}
          <div className="iv-col">
            <div className="iv-head">
              <div className="iv-head__eyebrow">
                <span className="iv-head__coin">{nm(symbol)}</span>
                <span className="iv-head__sym">{symbol}</span>
                {evalData && evalData.frescura && <Fresh frescura={evalData.frescura} noun="lectura" />}
              </div>
              <h1 className="iv-head__title">{nm(symbol)}</h1>
              {price != null && <div className="iv-head__price">${$(price)}<small>último cierre</small></div>}
            </div>
          </div>

          {/* ③ gráfico — ancho */}
          <div className="iv-wide">
            {evalUnavailable && (!levels || levels.estado === 'no_disponible')
              ? <div className="iv-callout iv-callout--mute" style={{ marginTop: 0 }}>
                  <span className="iv-callout__icon">⧖</span>
                  <div><div className="iv-callout__t">No se pudo revisar esta moneda ahora</div>
                    <div className="iv-callout__s">Binance no respondió. Es un problema de la herramienta, no de la moneda — sin campos en blanco fingiendo datos.</div></div>
                </div>
              : <Chart levels={levels} evalData={evalData} plan={plan} initLayers={chartLayers} />}
          </div>

          {/* ④ narrativa */}
          <div className="iv-col">
            <div className="iv-narr">
              <VidaBlock evalData={evalData} />
              <ParedesBlock levels={levels} />
              <JugadaBlock plan={plan} />
            </div>

            {/* ⑤ tu jugada ahora */}
            <section className="iv-sec" id="idea-jugada-now">
              <div className="iv-sec__h">Tu jugada ahora</div>
              <h2 className="iv-sec__q">El estado vivo de tu plan</h2>
              <PlayNow play={play} />
            </section>

            {/* ⑥ quién está detrás */}
            <section className="iv-sec" id="idea-quien">
              <div className="iv-sec__h">Quién está detrás</div>
              <h2 className="iv-sec__q">¿Se sabe quién sostiene el proyecto?</h2>
              <Dossier dossier={dossier} loading={dossierLoading} />
            </section>

            {/* ⑦ noticias — vacío honesto */}
            <section className="iv-sec" id="idea-noticias">
              <div className="iv-sec__h">Lo último que se dijo</div>
              <div className="news__empty">Las noticias de esta moneda aún no están conectadas.</div>
            </section>

            {/* ⑧ footer */}
            <div className="iv-foot">
              <button className="iv-foot__btn">← Mirar otra moneda</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

window.IdeaView = IdeaView;
window.RegimeFrame = RegimeFrame;
window.RegimeStrip = RegimeStrip;
window.PlayNow = PlayNow;
window.SP3Fresh = Fresh;
window.SP3Prop = Prop;
