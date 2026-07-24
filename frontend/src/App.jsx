import { useEffect, useState } from 'react'
import {
  ArrowRight, BookOpen, CheckCircle2, ChevronLeft, ChevronRight,
  GraduationCap, ListFilter, LoaderCircle, Plus, Search, Send, Trash2,
} from 'lucide-react'
import { searchExperiences, uploadExperience } from './api.js'
import ChatPage from './ChatPage.jsx'

const emptyForm = {
  graduate_school: '', country: '', apply_school: '', apply_program: '',
  gpa: '', class_rank: '', class_size: '', review: '',
  experience: [{ item: '', result: '' }],
}

function Field({ label, required, hint, children }) {
  return (
    <label className="field">
      <span>{label}{required && <b> *</b>}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  )
}

function UploadPage({ onView }) {
  const [form, setForm] = useState(emptyForm)
  const [status, setStatus] = useState({ type: '', message: '' })
  const [submitting, setSubmitting] = useState(false)

  const update = (key, value) => setForm((current) => ({ ...current, [key]: value }))
  const updateItem = (index, key, value) => setForm((current) => ({
    ...current,
    experience: current.experience.map((row, i) => i === index ? { ...row, [key]: value } : row),
  }))

  async function submit(event) {
    event.preventDefault()
    setStatus({ type: '', message: '' })
    if ((form.class_rank && !form.class_size) || (!form.class_rank && form.class_size)) {
      setStatus({ type: 'error', message: '系排與系人數請一起填寫。' })
      return
    }
    if (form.class_rank && Number(form.class_rank) > Number(form.class_size)) {
      setStatus({ type: 'error', message: '系排不能大於系人數。' })
      return
    }
    const items = form.experience.filter((row) => row.item.trim() || row.result.trim())
    if (items.some((row) => !row.item.trim() || !row.result.trim())) {
      setStatus({ type: 'error', message: '每一筆經歷都需要填寫項目與結果。' })
      return
    }
    const payload = {
      ...form,
      gpa: form.gpa === '' ? null : Number(form.gpa),
      class_rank: form.class_rank === '' ? null : Number(form.class_rank),
      class_size: form.class_size === '' ? null : Number(form.class_size),
      experience: items,
    }
    setSubmitting(true)
    try {
      await uploadExperience(payload)
      setForm(emptyForm)
      setStatus({ type: 'success', message: '謝謝你的分享！經驗已成功保存。' })
    } catch (error) {
      setStatus({ type: 'error', message: error.message })
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="page-shell">
      <section className="page-heading">
        <div className="eyebrow"><Send size={15} /> 分享經驗</div>
        <h1>你的申請歷程，<br />可能是別人的一盞燈。</h1>
        <p>分享真實背景與心得，讓下一位申請者少一點不確定。</p>
      </section>

      <form className="form-card" onSubmit={submit}>
        <div className="section-title"><span>01</span><div><h2>基本背景</h2><p>告訴我們你從哪裡出發</p></div></div>
        <div className="form-grid">
          <Field label="畢業學校" required><input required maxLength="200" value={form.graduate_school} onChange={(e) => update('graduate_school', e.target.value)} placeholder="例：National Taiwan University" /></Field>
          <Field label="國籍" required><input required maxLength="100" value={form.country} onChange={(e) => update('country', e.target.value)} placeholder="例：Taiwan" /></Field>
          <Field label="GPA" hint="接受 4.0、4.3、百分制等數值"><input type="number" min="0" max="100" step="0.01" value={form.gpa} onChange={(e) => update('gpa', e.target.value)} placeholder="例：3.82" /></Field>
          <div className="rank-group">
            <Field label="系排"><input type="number" min="1" value={form.class_rank} onChange={(e) => update('class_rank', e.target.value)} placeholder="名次" /></Field>
            <span>/</span>
            <Field label="系人數"><input type="number" min="1" value={form.class_size} onChange={(e) => update('class_size', e.target.value)} placeholder="總人數" /></Field>
          </div>
        </div>

        <div className="divider" />
        <div className="section-title"><span>02</span><div><h2>申請目標</h2><p>這份經驗對應的學校與學程</p></div></div>
        <div className="form-grid">
          <Field label="申請學校" required><input required maxLength="200" value={form.apply_school} onChange={(e) => update('apply_school', e.target.value)} placeholder="例：Carnegie Mellon University" /></Field>
          <Field label="申請 Program" required><input required maxLength="200" value={form.apply_program} onChange={(e) => update('apply_program', e.target.value)} placeholder="例：MS in Computer Science" /></Field>
        </div>

        <div className="divider" />
        <div className="section-title section-title-row"><span>03</span><div><h2>經歷與結果</h2><p>可自由新增考試、研究、實習或申請結果</p></div><button className="text-button" type="button" onClick={() => update('experience', [...form.experience, { item: '', result: '' }])}><Plus size={16} /> 新增項目</button></div>
        <div className="experience-editor">
          {form.experience.map((row, index) => (
            <div className="experience-row" key={index}>
              <input value={row.item} onChange={(e) => updateItem(index, 'item', e.target.value)} placeholder="項目，例如 TOEFL、Research" maxLength="100" />
              <ArrowRight size={18} />
              <input value={row.result} onChange={(e) => updateItem(index, 'result', e.target.value)} placeholder="結果，例如 108、2 years" maxLength="500" />
              <button type="button" className="icon-button" aria-label="刪除項目" disabled={form.experience.length === 1} onClick={() => update('experience', form.experience.filter((_, i) => i !== index))}><Trash2 size={17} /></button>
            </div>
          ))}
        </div>

        <Field label="申請心得" required hint="建議分享準備策略、踩過的坑，或你希望當時有人告訴你的事。">
          <textarea required minLength="1" maxLength="10000" rows="7" value={form.review} onChange={(e) => update('review', e.target.value)} placeholder="寫下你的申請歷程……" />
        </Field>

        {status.message && <div className={`notice ${status.type}`} role="status">{status.type === 'success' && <CheckCircle2 size={18} />}{status.message}</div>}
        <div className="form-actions">
          <button type="button" className="secondary-button" onClick={onView}>查看大家的經驗</button>
          <button className="primary-button" disabled={submitting}>{submitting ? <LoaderCircle className="spin" size={18} /> : <Send size={18} />}{submitting ? '正在送出' : '送出經驗'}</button>
        </div>
      </form>
    </main>
  )
}

function ExperienceCard({ data }) {
  const date = new Intl.DateTimeFormat('zh-TW', { year: 'numeric', month: 'short', day: 'numeric' }).format(new Date(data.created_at))
  return (
    <article className="experience-card">
      <div className="card-top"><div><span className="program-label">{data.apply_program}</span><h3>{data.apply_school}</h3></div><time>{date}</time></div>
      <div className="profile-strip">
        <div><small>畢業學校</small><strong>{data.graduate_school}</strong></div>
        <div><small>國籍</small><strong>{data.country}</strong></div>
        <div><small>GPA</small><strong>{data.gpa ?? '未提供'}</strong></div>
        <div><small>系排</small><strong>{data.class_rank ? `${data.class_rank} / ${data.class_size}` : '未提供'}</strong></div>
      </div>
      {data.experience.length > 0 && <div className="tag-list">{data.experience.map((item, index) => <span key={`${item.item}-${index}`}><b>{item.item}</b>{item.result}</span>)}</div>}
      <p className="review">{data.review}</p>
    </article>
  )
}

function SearchPage() {
  const [school, setSchool] = useState('')
  const [program, setProgram] = useState('')
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [offset, setOffset] = useState(0)
  const limit = 10

  async function runSearch(event, nextOffset = 0) {
    event?.preventDefault()
    if (!school.trim()) return
    setLoading(true); setError('')
    try {
      const data = await searchExperiences({ school: school.trim(), program, limit, offset: nextOffset })
      setResult(data); setOffset(nextOffset)
    } catch (err) { setError(err.message) } finally { setLoading(false) }
  }

  return (
    <main className="page-shell search-page">
      <section className="page-heading centered">
        <div className="eyebrow"><ListFilter size={15} /> 經驗資料庫</div>
        <h1>看看走過這條路的人，<br />怎麼準備。</h1>
        <p>輸入申請學校，找到相同目標的真實經驗。</p>
      </section>
      <form className="search-box" onSubmit={(e) => runSearch(e, 0)}>
        <Search size={21} />
        <input required value={school} onChange={(e) => setSchool(e.target.value)} placeholder="輸入申請學校，例如 Stanford University" />
        <input className="program-search" value={program} onChange={(e) => setProgram(e.target.value)} placeholder="Program（選填）" />
        <button disabled={loading}>{loading ? <LoaderCircle className="spin" size={18} /> : '搜尋經驗'}</button>
      </form>
      {error && <div className="notice error results-notice">{error}</div>}
      {loading && <div className="state-panel"><LoaderCircle className="spin" /><p>正在翻找經驗……</p></div>}
      {!loading && result && result.items.length === 0 && <div className="state-panel"><BookOpen /><h2>還沒有相關分享</h2><p>換個學校名稱試試，或成為第一位分享的人。</p></div>}
      {!loading && result?.items.length > 0 && <section className="results"><div className="results-heading"><h2>{school} 的申請經驗</h2><span>共 {result.total} 筆</span></div>{result.items.map((item) => <ExperienceCard key={item.id} data={item} />)}<div className="pagination"><button disabled={offset === 0} onClick={() => runSearch(null, Math.max(0, offset - limit))}><ChevronLeft size={17} /> 上一頁</button><span>第 {Math.floor(offset / limit) + 1} 頁</span><button disabled={offset + limit >= result.total} onClick={() => runSearch(null, offset + limit)}>下一頁 <ChevronRight size={17} /></button></div></section>}
    </main>
  )
}

function App() {
  const hash = window.location.hash
  const route = hash === '#/search' ? 'search' : hash === '#/chat' ? 'chat' : 'upload'
  const [, rerender] = useState(0)
  useEffect(() => { const change = () => rerender((n) => n + 1); window.addEventListener('hashchange', change); return () => window.removeEventListener('hashchange', change) }, [])
  const navigate = (page) => { window.location.hash = `#/${page}` }
  return <><header className="site-header"><a className="brand" href="#/upload"><span><GraduationCap size={22} /></span><div>留學經驗站<small>STUDY ABROAD STORIES</small></div></a><nav><a className={route === 'upload' ? 'active' : ''} href="#/upload">分享經驗</a><a className={route === 'search' ? 'active' : ''} href="#/search">查詢經驗</a><a className={route === 'chat' ? 'active' : ''} href="#/chat">AI 諮詢</a></nav></header>{route === 'chat' ? <ChatPage /> : route === 'search' ? <SearchPage /> : <UploadPage onView={() => navigate('search')} />}<footer>每一份經驗都來自個人分享，僅供申請準備參考。</footer></>
}

export default App
