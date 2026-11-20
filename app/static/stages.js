import {drill, el, entries, fault, hour, jump, line, pick, put, q, stamp, state, table, words} from './paper.js';

const raw = (row) => row.raw.map((value) => JSON.stringify(value)).join(' · ');
const issue = (target, rows) => {
  if (!rows.length) target.replaceChildren(el('p','无隔离或迟报记录。','empty'));
  else target.replaceChildren(...rows.map((row) => {
    const ticket = el('div',null,'proof-slip');
    ticket.append(el('strong',`${line(row)} · ${row.code ?? row.flags?.join(' ') ?? '—'}`), el('p',row.note ?? row.fields?.memo ?? raw(row)));
    return ticket;
  }));
};
const matched = (slot) => Boolean(slot.upper && slot.lower);
const rowLabel = (value) => value?.split(':').at(-1) ?? '—';

async function intake({get, base, signal}) {
  const data = await get(`${base}/intake`);
  q('#intake-summary').replaceChildren(entries([['图档编码',data.encoding],['记录段',data.segments],
    ['物理行',data.physical],['数据记录',data.declared]]));
  q('#intake-isolation').replaceChildren(entries(Object.entries(data.isolation)),
    ...(!Object.keys(data.isolation).length ? [el('p','本档无隔离行。','empty')] : []));
  const filter = q('#reading-filter');
  const draw = () => {
    const rows = data.rows.filter((row) => filter.value === 'all' || (filter.value === 'late' ? row.flags?.includes('LATE_RECEIPT') : row.disposition === filter.value));
    table(q('#readings'),rows,[['行',line,'num'],['探头',(row) => row.fields?.probe,'mono'],
      ['测量',(row) => stamp(row.observed_utc),'mono'],['收报',(row) => stamp(row.received_utc),'mono'],
      ['温度 °C',(row) => row.celsius?.[0],'num'],['湿度 %RH',(row) => row.relative_humidity?.[0],'num'],
      ['状态',(row) => state(row.disposition)],['原因码',(row) => row.code ?? row.flags?.join(' '),'mono'],['原始字段',raw]]);
  };
  filter.addEventListener('change',draw,{signal});
  draw();
}

async function assignment({get, base, signal}) {
  const data = await get(`${base}/assignment`);
  const form = q('#lookup-form');
  const result = q('#lookup-result');
  const lookup = async () => {
    const button = q('button',form);
    button.disabled = true;
    try {
      const found = await get(`${base}/assignment/lookup?${new URLSearchParams(new FormData(form))}`);
      if (signal.aborted) return;
      const d = found.deployment;
      result.replaceChildren(d ? entries([['展柜',d.cabinet],['层位',words[d.level]],['部署号',d.id],['起',stamp(d.start)],['止',stamp(d.end)]]) : state(found.code));
    } catch (error) {
      if (!signal.aborted) fault(result,error);
    } finally {
      if (!signal.aborted) button.disabled = false;
    }
  };
  form.addEventListener('submit',(event) => { event.preventDefault(); lookup(); },{signal});
  lookup();
  table(q('#deployments'),data.windows,[['部署号','id','mono'],['探头','probe','mono'],['展柜','cabinet','mono'],
    ['层位',(item) => words[item.level]],['起',(item) => stamp(item.start),'mono'],['止',(item) => stamp(item.end),'mono']]);
  issue(q('#assignment-issues'),[...data.issues,...data.late.filter((row) => !data.issues.some((item) => item.id === row.id))]);
  for (const overlap of data.conflicts) q('#assignment-issues').append(entries([['重叠部署',overlap.deployments.join(' · ')],['起',stamp(overlap.start)],['止',stamp(overlap.end)]]));
}

async function pairing({get, base, cabinet}) {
  const data = await get(`${base}/pairing/${cabinet}`);
  const count = data.slots.filter(matched).length;
  q('#pair-summary').replaceChildren(entries([['容差',`${data.tolerance_seconds} s`],['应配',data.slots.length],['成对',count],['无配对',data.slots.length-count]]));
  table(q('#pairs'),data.slots,[['柜时',(slot) => stamp(slot.start),'mono'],['上层行',(slot) => rowLabel(slot.upper),'num'],
    ['下层行',(slot) => rowLabel(slot.lower),'num'],['时差 s','drift','num'],['证据',(slot) => state(slot.code ?? '已配对')]]);
  table(q('#unpaired'),data.unused,[['行',(row) => rowLabel(row.id),'num'],['原因',(row) => words[row.code]]]);
}

function specimenReading(row) {
  const slip = el('article',null,'proof-slip');
  slip.tabIndex = -1;
  slip.id = `proof-${row.deployment.level}`;
  slip.append(el('strong',`${words[row.deployment.level]} · ${row.id} · ${row.probe}`),
    el('p',`${stamp(row.observed_utc)} 测量 · ${stamp(row.received_utc)} 收报`),
    el('p',`${row.fields.temp} ${row.fields.temp_unit} ± ${row.fields.temp_sigma}  /  ${row.fields.rh} ${row.fields.rh_unit}`));
  if (row.flags.length) slip.append(el('p',row.flags.map((flag) => words[flag]).join('、')));
  if (row.fields.memo) slip.append(el('p',row.fields.memo));
  return slip;
}

function renderCut(spec, slot, byLevel) {
  put('#cut-label',spec.label);
  const target = q('#section-cut');
  const drawing = el('div',null,'cut-drawing');
  const frame = el('div',null,'case-frame');
  const ruler = el('div',null,'measure-ruler');
  const ceiling = Math.ceil((spec.upper_mm + 150) / 500) * 500;
  for (let tick=0; tick<=ceiling; tick+=500) {
    const index = el('span',tick);
    index.style.bottom = `${tick / ceiling * 100}%`;
    ruler.append(index);
  }
  frame.append(el('div',null,'case-floor'));
  for (const level of ['upper','lower']) {
    const row = byLevel[level];
    const mark = el('div',null,'location');
    mark.style.bottom = `${spec[`${level}_mm`] / ceiling * 100}%`;
    const marker = el('button',`${words[level]} · ${row?.probe ?? '缺证'}`);
    marker.type = 'button';
    marker.disabled = !row;
    marker.addEventListener('click', () => q(`#proof-${level}`).focus());
    mark.append(marker,el('span',row ? `${Number(row.celsius[0]).toFixed(2)} °C` : '—'));
    frame.append(mark);
  }
  drawing.append(ruler,frame);
  const caption = el('div',null,'cut-direction');
  caption.append(el('span',`层间距 ${slot.gap_mm} mm`),el('span',`配对时差 ${slot.drift == null ? '—' : `${slot.drift} s`}`));
  target.replaceChildren(drawing,caption);
}

function interval(label, unit, metric) {
  const block = el('div',null,'interval');
  const title = el('header');
  title.append(el('span',label),el('b',metric ? `${metric.value} ${unit}` : '—'));
  block.append(title);
  if (!metric) return block;
  const [low, high, limit] = [metric.low,metric.high,metric.limit].map(Number);
  const scale = Math.max(Math.abs(low),Math.abs(high),limit) * 1.1;
  const percent = (value) => 50 + 50 * value / scale;
  const track = el('div',null,'range-track');
  track.setAttribute('aria-hidden','true');
  for (const [left,right,css] of [[-limit,limit,'safe-band'],[low,high,metric.verdict === 'ABNORMAL' ? 'observed-band abnormal-band' : 'observed-band']]) {
    const band = el('span',null,css);
    band.style.left = `${percent(left)}%`;
    band.style.width = `${percent(right)-percent(left)}%`;
    track.append(band);
  }
  block.append(el('p',`[${metric.low}, ${metric.high}] · ±${metric.limit}`,'interval-label'),track,state(metric.verdict));
  return block;
}

async function gradient({get, base, cabinet, params, signal}) {
  const data = await get(`${base}/gradient/${cabinet}`);
  const filter = q('#grade-filter');
  const target = q('#grade-evidence');
  const load = drill(target,signal);
  const select = (slot) => {
    put('#grade-time',stamp(slot.start));
    renderCut(data.cabinet,slot,{});
    return load(() => get(`${base}/gradient/${cabinet}/evidence?${new URLSearchParams({at:slot.start})}`), (proof) => {
      const title = el('div',null,'proof-title');
      title.append(state(proof.slot.state));
      if (proof.slot.code) title.append(el('span',words[proof.slot.code] ?? proof.slot.code));
      target.replaceChildren(title, interval('温度梯度','°C/m',proof.slot.temperature), interval('湿度梯度','%RH/m',proof.slot.humidity),
        ...proof.rows.map(specimenReading));
      renderCut(proof.cabinet,proof.slot,Object.fromEntries(proof.rows.map((row) => [row.deployment.level,row])));
    });
  };
  const draw = () => {
    const slots = data.slots.filter((slot) => filter.value === 'all' || filter.value === slot.state);
    table(q('#grade-slots'),slots,[['柜时',(slot) => pick(hour(slot.start),() => select(slot),stamp(slot.start)),'mono'],
      ['判级',(slot) => state(slot.state)]]);
    const wanted = slots.findIndex((slot) => slot.start === params.get('at'));
    if (slots.length) q('#grade-slots').querySelectorAll('button')[Math.max(0,wanted)].click();
    else load(async () => null, () => {
      q('#section-cut').replaceChildren();
      put('#grade-time',null);
      target.replaceChildren(el('p','当前筛选无柜时。','empty'));
    });
  };
  filter.addEventListener('change',draw,{signal});
  draw();
}

async function episodes({get, base, cabinet, run, signal}) {
  const data = await get(`${base}/episodes/${cabinet}`);
  const target = q('#episode-detail');
  const load = drill(target,signal);
  const show = (id) => load(() => get(`${base}/episodes/${cabinet}/${id}`),(item) => {
    const links = el('div',null,'jump-list');
    for (const entry of item.evidence) {
      links.append(jump(`${hour(entry.start)} · 原行 ${rowLabel(entry.upper)}/${rowLabel(entry.lower)}`, '/', {run:run.id,cabinet,at:entry.start}));
    }
    target.replaceChildren(entries([['异常段',item.id],['时长',`${item.hours} h`],['指标',item.axes.map((axis) => words[axis]).join('、')]]),links);
  });
  q('#episode-summary').replaceChildren(entries([['总段数',data.length],['持续',data.filter((item) => item.kind === 'SUSTAINED').length],
    ['瞬时',data.filter((item) => item.kind === 'TRANSIENT').length],['持续门槛',`${run.sustained_hours} h`]]));
  table(q('#episodes'), data,[['段号',(row) => pick(row.id,() => show(row.id)),'mono'],['判级',(row) => state(row.kind)],
    ['起',(row) => stamp(row.start),'mono'],['止',(row) => stamp(row.end),'mono'],['时长 h','hours','num']]);
  if (data.length) q('#episodes button').click();
  else target.replaceChildren(el('p','此柜工作时段无异常。','empty'));
}

function audit({audit,run}) {
  const hours = audit.hours;
  const rows = audit.row_audit;
  q('#audit-hours').replaceChildren(entries([['应监测',hours.expected],['稳定',hours.stable],['异常',hours.abnormal],['缺证',hours.missing],
    ['柜时核验',hours.balanced?'守恒':'有差异'],['图档行账',rows.balanced?'守恒':'有差异']]),
    entries([['采信',rows.accepted],['重传',rows.duplicate],['隔离',rows.isolated],['空行',rows.blank],['档头',rows.header]]));
  table(q('#tickets'),audit.tickets,[['工单',(item) => item.slot ? jump(item.id,'/',{run:run.id,cabinet:item.cabinet,at:item.slot}) : item.id,'mono'],
    ['展柜','cabinet','mono'],['馆内时刻',(item) => stamp(item.at_utc),'mono'],['勾稽',(item) => state(item.code)],
    ['关联段','episode','mono'],['原行',(item) => item.evidence.map(rowLabel).join(' / '),'mono']],'本次无报警工单。');
  const present = audit.unreported.length ? audit.unreported.join('、') : '无';
  q('#unreported').replaceChildren(entries([['缺工单',present],['已核销',audit.ticket_counts.MATCHED ?? 0],
    ['工单差项',audit.tickets.length - (audit.ticket_counts.MATCHED ?? 0)],['柜时缺键',hours.missing_keys.length],
    ['柜时多键',hours.extra_keys.length],['柜时重键',hours.duplicate_keys.length]]));
}

function archive({history,run}) {
  const list = el('div',null,'history-list');
  for (const item of history) {
    const link = jump(`${item.shift} · ${item.id.slice(0,8)}`,'/archive',{run:item.id});
    if (item.id === run.id) link.setAttribute('aria-current','true');
    list.append(link);
  }
  q('#history').replaceChildren(list);
  q('#digests').classList.add('digest-list');
  q('#digests').replaceChildren(entries([['归卷号',run.id],['原档 SHA-256',run.hashes.file_sha256],
    ['契约 SHA-256',run.hashes.contract_sha256],['结果 SHA-256',run.result_sha256]]));
  q('#archive-spec').replaceChildren(entries([['输入',run.file],['规则',run.policy],['配准容差',`${run.tolerance_seconds} s`],
    ['持续门槛',`${run.sustained_hours} h`],['耗时',`${run.elapsed_ms} ms`],['归卷柜号',run.cabinets.map((item) => item.id).join('、')]]));
}

function statePill(code) {
  return state(code);
}

function spacingForm(run) {
  const host = q('#remeasure-rows');
  host.replaceChildren();
  for (const cabinet of run.cabinets) {
    const block = el('fieldset',null,'spacing-cabinet');
    const legend = el('legend',`${cabinet.id} · 建档间距 ${cabinet.upper_mm - cabinet.lower_mm} mm`);
    const upper = el('input'); upper.type = 'number'; upper.name = `${cabinet.id}.upper_mm`;
    upper.min = '150'; upper.max = '5000'; upper.value = cabinet.upper_mm; upper.required = true;
    const lower = el('input'); lower.type = 'number'; lower.name = `${cabinet.id}.lower_mm`;
    lower.min = '0'; lower.max = '4500'; lower.value = cabinet.lower_mm; lower.required = true;
    const evidence = el('input'); evidence.name = `${cabinet.id}.evidence`; evidence.maxLength = '200';
    evidence.placeholder = '复尺证据编号'; evidence.required = true;
    const measurer = el('input'); measurer.name = `${cabinet.id}.measurer`; measurer.maxLength = '64';
    measurer.placeholder = '测量人'; measurer.required = true;
    const grid = el('div',null,'spacing-grid');
    const lab = (text, input) => { const label = el('label',text); label.append(input); return label; };
    grid.append(lab('上层高度 mm',upper), lab('下层高度 mm',lower), lab('证据编号',evidence), lab('测量人',measurer));
    block.append(legend, grid);
    host.append(block);
  }
}

function spacingConclusion(report) {
  const target = q('#spacing-conclusion');
  q('#spacing-conclusion-meta').textContent = report.measure_no ?? '—';
  if (report.status === 'REJECTED') {
    const box = el('div',null,'spacing-reject');
    box.append(el('p',report.message,'fault'));
    box.append(entries([['复尺单号',report.measure_no],['处理','保留旧版尺寸与原读数，旧证未改动']]));
    const checks = el('table',null,'ledger');
    const head = checks.createTHead().insertRow();
    for (const name of ['展柜','上层 mm','下层 mm','证据','测量人','核验']) head.append(el('th',name));
    const body = checks.createTBody();
    for (const item of report.evidence_checks) {
      const row = body.insertRow();
      for (const value of [item.cabinet,item.upper_mm,item.lower_mm,item.evidence || '—',item.measurer || '—']) {
        row.insertCell().textContent = value ?? '—';
      }
      row.insertCell().append(statePill(item.status));
    }
    box.append(checks);
    target.replaceChildren(box);
    return;
  }

  const fragment = document.createDocumentFragment();
  // 每柜尺寸与判级变化
  for (const cabinet of report.cabinets) {
    const block = el('section',null,'spacing-block');
    block.append(entries([
      ['展柜',`${cabinet.cabinet} · ${cabinet.label}`],
      ['间距',`${cabinet.before_gap_mm} → ${cabinet.after_gap_mm} mm`],
      ['上下高度',`上 ${cabinet.before_dimensions.upper_mm}/${cabinet.after_dimensions.upper_mm} mm，下 ${cabinet.before_dimensions.lower_mm}/${cabinet.after_dimensions.lower_mm} mm`],
      ['复尺证据',`${cabinet.evidence} · ${cabinet.measurer}`],
      ['判级变化柜时',String(cabinet.changed_hours)],
      ['稳定/异常/缺证',`${cabinet.hours_before.stable}/${cabinet.hours_before.abnormal}/${cabinet.hours_before.missing} → ${cabinet.hours_after.stable}/${cabinet.hours_after.abnormal}/${cabinet.hours_after.missing}`],
    ]));
    if (cabinet.grade_changes.length) {
      block.append(el('h3','改判柜时','spacing-subhead'));
      const grid = el('table',null,'ledger');
      const head = grid.createTHead().insertRow();
      for (const name of ['柜时','原判级','新判级','原间距 mm','新间距 mm','上层原行','下层原行']) head.append(el('th',name));
      const body = grid.createTBody();
      for (const change of cabinet.grade_changes) {
        const row = body.insertRow();
        row.insertCell().textContent = hour(change.hour);
        row.insertCell().append(statePill(change.before_state));
        row.insertCell().append(statePill(change.after_state));
        row.insertCell().textContent = change.before_gap_mm;
        row.insertCell().textContent = change.after_gap_mm;
        row.insertCell().textContent = rowLabel(change.upper);
        row.insertCell().textContent = rowLabel(change.lower);
      }
      block.append(grid);
    } else {
      block.append(el('p','该柜无柜时判级变化。','empty'));
    }
    fragment.append(block);
  }

  // 持续异常变化
  const episodes = report.episodes;
  const episodeBox = el('section',null,'spacing-block');
  episodeBox.append(el('h3','持续异常核对','spacing-subhead'));
  const epRows = [
    ...episodes.created.map((item) => ['新增',item.id,`${hour(item.start)}–${hour(item.end)}`,item.kind,item.hours]),
    ...episodes.removed.map((item) => ['消失',item.id,`${hour(item.start)}–${hour(item.end)}`,item.kind,item.hours]),
    ...episodes.updated.map((item) => ['变更',item.after.id,`${hour(item.after.start)}–${hour(item.after.end)}`,`${item.before.kind}→${item.after.kind}`,`${item.before.hours}→${item.after.hours}`]),
  ];
  if (epRows.length) {
    const grid = el('table',null,'ledger');
    const head = grid.createTHead().insertRow();
    for (const name of ['变化','段号','时段','类别','时长 h']) head.append(el('th',name));
    for (const values of epRows) {
      const row = grid.createTBody().insertRow();
      for (const value of values) row.insertCell().textContent = value;
    }
    episodeBox.append(grid);
  } else episodeBox.append(el('p','持续异常段无变化。','empty'));
  fragment.append(episodeBox);

  // 工单核对变化
  const ticketBox = el('section',null,'spacing-block');
  ticketBox.append(el('h3','工单核对变化','spacing-subhead'));
  if (report.tickets.changed.length) {
    const grid = el('table',null,'ledger');
    const head = grid.createTHead().insertRow();
    for (const name of ['工单','展柜','时刻','原核对','新核对','原关联段','新关联段']) head.append(el('th',name));
    const body = grid.createTBody();
    for (const item of report.tickets.changed) {
      const row = body.insertRow();
      row.insertCell().textContent = item.ticket;
      row.insertCell().textContent = item.cabinet;
      row.insertCell().textContent = stamp(item.at_utc);
      row.insertCell().append(statePill(item.before_code));
      row.insertCell().append(statePill(item.after_code));
      row.insertCell().textContent = item.before_episode ?? '—';
      row.insertCell().textContent = item.after_episode ?? '—';
    }
    ticketBox.append(grid);
  } else ticketBox.append(el('p','工单核对结论无变化。','empty'));
  ticketBox.append(entries([
    ['未报持续段（原）',report.tickets.before_unreported.join('、') || '无'],
    ['未报持续段（新）',report.tickets.after_unreported.join('、') || '无'],
    ['新增缺工单',report.tickets.unreported_added.join('、') || '无'],
    ['已核销缺工单',report.tickets.unreported_cleared.join('、') || '无'],
  ]));
  fragment.append(ticketBox);

  // 守恒总账
  const ledger = el('section',null,'spacing-block');
  const hb = report.hours_before, ha = report.hours_after;
  ledger.append(el('h3','柜时总账（全班同一版尺寸）','spacing-subhead'));
  ledger.append(entries([
    ['稳定',`${hb.stable} → ${ha.stable}`], ['异常',`${hb.abnormal} → ${ha.abnormal}`],
    ['缺证',`${hb.missing} → ${ha.missing}`], ['应监测',`${hb.expected} → ${ha.expected}`],
    ['基线结果摘要',report.baseline_result_sha256.slice(0,16)],
    ['新版结果摘要',report.revised_result_sha256.slice(0,16)],
  ]));
  fragment.append(ledger);
  target.replaceChildren(fragment);
}

async function spacing({get, base, run, signal}) {
  spacingForm(run);
  const form = q('#spacing-form');
  const historyHost = q('#spacing-history');

  async function loadHistory(activeId = null) {
    const records = await get(`${base}/spacing/reviews`);
    if (signal.aborted) return;
    if (!records.length) {
      historyHost.replaceChildren(el('p','暂无复核版本。','empty'));
      return;
    }
    const list = el('div',null,'history-list');
    for (const item of records) {
      const lineBox = el('div',null,'spacing-history-row');
      const row = el('button',`${item.measure_no} · ${item.status === 'PUBLISHED' ? '正式' : '未通过'}`,'table-action');
      row.type = 'button';
      if (item.id === activeId) row.setAttribute('aria-pressed','true');
      row.addEventListener('click',() => spacingConclusion(item.report));
      lineBox.append(row);
      if (item.status === 'PUBLISHED') {
        const verifyBtn = el('button','复算','table-action');
        verifyBtn.type = 'button';
        verifyBtn.addEventListener('click', async () => {
          verifyBtn.disabled = true;
          try {
            const result = await fetch(`${base}/spacing/reviews/${item.id}/verify`,{method:'POST',signal});
            const body = await result.json();
            if (!result.ok) throw new Error(body?.message || '复算未通过');
            q('#feedback').hidden = false;
            q('#feedback').classList.remove('error');
            q('#feedback').textContent = `复算一致 · ${body.result_sha256.slice(0,12)}`;
          } catch (error) {
            if (signal.aborted) return;
            q('#feedback').hidden = false;
            q('#feedback').classList.add('error');
            q('#feedback').textContent = error.message;
          } finally {
            verifyBtn.disabled = false;
          }
        });
        lineBox.append(verifyBtn);
      }
      list.append(lineBox, el('p',`${stamp(item.created)} · ${item.id.slice(0,12)}`,'mono'));
    }
    historyHost.replaceChildren(list);
    const latest = records[records.length - 1];
    if (activeId === null) spacingConclusion(latest.report);
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const data = new FormData(form);
    const cabinets = run.cabinets.map((cabinet) => {
      const prefix = `${cabinet.id}.`;
      const upper = data.get(prefix + 'upper_mm');
      const lower = data.get(prefix + 'lower_mm');
      return {cabinet: cabinet.id,
        upper_mm: upper === '' ? null : Number(upper),
        lower_mm: lower === '' ? null : Number(lower),
        evidence: (data.get(prefix + 'evidence') ?? '').toString().trim(),
        measurer: (data.get(prefix + 'measurer') ?? '').toString().trim()};
    });
    const payload = {measure_no: data.get('measure_no'), cabinets};
    q('#feedback').hidden = false;
    q('#feedback').classList.remove('error');
    q('#feedback').textContent = '正在复算整班判级、持续异常与工单…';
    try {
      const response = await fetch(`${base}/spacing/reviews`,{method:'POST',
        headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload), signal});
      const body = await response.json();
      if (!response.ok) throw new Error(body?.message || body?.report?.message || '复核未通过，已保留旧版。');
      q('#feedback').classList.remove('error');
      q('#feedback').textContent = body.reused ? '同一复尺单，复用既有结论，未新增版本。' : '正式复核结论已生成，全班启用同一版尺寸。';
      spacingConclusion(body.report);
      await loadHistory(body.review_id);
    } catch (error) {
      if (signal.aborted) return;
      q('#feedback').classList.add('error');
      q('#feedback').textContent = error.message;
      // 失败也刷新留痕，并展示失败登记内容（错误响应体里带 report）
      const failed = await fetch(`${base}/spacing/reviews`,{signal}).then((r) => r.json());
      const latest = failed[failed.length - 1];
      if (latest) spacingConclusion(latest.report);
      await loadHistory();
    }
  },{signal});

  await loadHistory();
}

export const stages = {intake, assignment, pairing, gradient, episodes, audit, spacing, archive};
