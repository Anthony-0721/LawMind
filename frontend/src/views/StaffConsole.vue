<script setup>
import { computed, reactive, ref } from 'vue'
import { RouterLink } from 'vue-router'
import {
  createFaq,
  createLawyer,
  deleteConsultation,
  deleteFaq,
  getAdminMetrics,
  getConsultation,
  getSkills,
  getTraceTools,
  listConsultations,
  listFaqs,
  listLawyers,
  reloadKnowledge,
  staffLogin,
  toggleFaq,
  toggleLawyer,
  updateConsultationStatus,
  updateFaq,
  updateLawyer,
} from '../lib/lawApi'

const tabs = [
  { key: 'consultations', label: '咨询记录' },
  { key: 'lawyers', label: '律师管理' },
  { key: 'faqs', label: 'FAQ 管理' },
  { key: 'debug', label: '知识库/调试' },
]

const activeTab = ref('consultations')
const password = ref('')
const authenticated = ref(false)
const loginBusy = ref(false)
const loginError = ref('')

const records = ref([])
const recordsBusy = ref(false)
const recordsError = ref('')
const selectedRecord = ref(null)
const detailStatus = ref('')

const lawyers = ref([])
const lawyersBusy = ref(false)
const lawyersError = ref('')
const lawyerEditingId = ref('')
const lawyerForm = reactive({
  name: '',
  domain: 'general',
  specialties: '',
  intro: '',
  phone: '',
  wechat: '',
  email: '',
  active: true,
  sort_order: 0,
})

const faqs = ref([])
const faqsBusy = ref(false)
const faqsError = ref('')
const faqEditingId = ref('')
const faqForm = reactive({
  category: 'service',
  question: '',
  answer: '',
  keywords: '',
  active: true,
  sort_order: 0,
})

const metrics = ref(null)
const optionalDebug = reactive({ skills: null, trace: null })
const debugBusy = ref(false)
const debugError = ref('')
const reloadResult = ref(null)

const statusLabels = {
  PENDING: '待联系',
  CONTACTED: '已联系',
  BOOKED: '已预约',
  CLOSED: '已关闭',
}
const domainLabels = {
  dangerous_driving: '醉驾 / 危险驾驶',
  criminal_defense: '刑事辩护',
  labor_dispute: '劳动争议',
  marriage_family: '婚姻家事',
  contract_dispute: '合同纠纷',
  traffic_accident: '交通事故',
  civil_loan: '民间借贷 / 债务纠纷',
  lawyer_appointment: '预约律师 / 转人工',
  law_firm_service: '律所服务 / 收费 / 流程',
  other: '其他',
}

const debugSummary = computed(() => ({
  metrics: metrics.value,
  skills: optionalDebug.skills,
  traceTools: optionalDebug.trace,
}))

function errorMessage(error) {
  return error?.detail || error?.message || '请求失败'
}

function formatDomain(value) {
  return domainLabels[value] || value || '-'
}

function formatValue(value) {
  if (value === null || value === undefined || value === '') return '-'
  if (Array.isArray(value)) return value.join('、')
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function statusClass(status) {
  return `status-${String(status || '').toLowerCase()}`
}

async function login() {
  if (!password.value.trim()) {
    loginError.value = '请输入管理员密码'
    return
  }
  loginBusy.value = true
  loginError.value = ''
  try {
    await staffLogin(password.value)
    authenticated.value = true
    await Promise.all([loadConsultations(), loadLawyers(), loadFaqs(), loadDebug()])
  } catch (error) {
    authenticated.value = false
    loginError.value = `登录失败：${errorMessage(error)}`
  } finally {
    loginBusy.value = false
  }
}

function logout() {
  authenticated.value = false
  password.value = ''
  records.value = []
  selectedRecord.value = null
  lawyers.value = []
  faqs.value = []
  metrics.value = null
  debugError.value = ''
}

async function loadConsultations() {
  recordsBusy.value = true
  recordsError.value = ''
  try {
    records.value = await listConsultations(password.value)
  } catch (error) {
    recordsError.value = errorMessage(error)
  } finally {
    recordsBusy.value = false
  }
}

async function openRecord(record) {
  selectedRecord.value = null
  try {
    selectedRecord.value = await getConsultation(record.id, password.value)
    detailStatus.value = selectedRecord.value?.status || ''
  } catch (error) {
    recordsError.value = errorMessage(error)
  }
}

async function handleStatusChange(record) {
  try {
    selectedRecord.value = await updateConsultationStatus(record.id, detailStatus.value, password.value)
    detailStatus.value = selectedRecord.value?.status || ''
    await loadConsultations()
  } catch (error) {
    recordsError.value = `状态更新失败：${errorMessage(error)}`
  }
}

async function removeSelectedRecord() {
  if (!selectedRecord.value || !window.confirm('确认删除该咨询记录？')) return
  try {
    await deleteConsultation(selectedRecord.value.id, password.value)
    selectedRecord.value = null
    await loadConsultations()
  } catch (error) {
    recordsError.value = `删除失败：${errorMessage(error)}`
  }
}

function resetLawyerForm() {
  lawyerEditingId.value = ''
  lawyerForm.name = ''
  lawyerForm.domain = 'general'
  lawyerForm.specialties = ''
  lawyerForm.intro = ''
  lawyerForm.phone = ''
  lawyerForm.wechat = ''
  lawyerForm.email = ''
  lawyerForm.active = true
  lawyerForm.sort_order = 0
}

function editLawyer(lawyer) {
  lawyerEditingId.value = lawyer.id || ''
  lawyerForm.name = lawyer.name || ''
  lawyerForm.domain = lawyer.domain || 'general'
  lawyerForm.specialties = Array.isArray(lawyer.specialties) ? lawyer.specialties.join(', ') : (lawyer.specialties || '')
  lawyerForm.intro = lawyer.intro || ''
  lawyerForm.phone = lawyer.phone || ''
  lawyerForm.wechat = lawyer.wechat || ''
  lawyerForm.email = lawyer.email || ''
  lawyerForm.active = lawyer.active !== false
  lawyerForm.sort_order = Number(lawyer.sort_order || 0)
}

async function loadLawyers() {
  lawyersBusy.value = true
  lawyersError.value = ''
  try {
    lawyers.value = await listLawyers(password.value)
  } catch (error) {
    lawyersError.value = errorMessage(error)
  } finally {
    lawyersBusy.value = false
  }
}

async function submitLawyer() {
  if (!lawyerForm.name.trim()) {
    lawyersError.value = '律师姓名不能为空'
    return
  }
  const payload = {
    name: lawyerForm.name.trim(),
    domain: lawyerForm.domain.trim() || 'general',
    specialties: lawyerForm.specialties.split(',').map((item) => item.trim()).filter(Boolean),
    intro: lawyerForm.intro.trim(),
    phone: lawyerForm.phone.trim(),
    wechat: lawyerForm.wechat.trim(),
    email: lawyerForm.email.trim(),
    active: lawyerForm.active,
    sort_order: Number(lawyerForm.sort_order || 0),
  }
  try {
    if (lawyerEditingId.value) {
      await updateLawyer(lawyerEditingId.value, payload, password.value)
    } else {
      await createLawyer(payload, password.value)
    }
    resetLawyerForm()
    await loadLawyers()
  } catch (error) {
    lawyersError.value = `律师保存失败：${errorMessage(error)}`
  }
}

async function toggleLawyerRecord(lawyer) {
  try {
    await toggleLawyer(lawyer.id, !lawyer.active, password.value)
    await loadLawyers()
  } catch (error) {
    lawyersError.value = `启停失败：${errorMessage(error)}`
  }
}

function resetFaqForm() {
  faqEditingId.value = ''
  faqForm.category = 'service'
  faqForm.question = ''
  faqForm.answer = ''
  faqForm.keywords = ''
  faqForm.active = true
  faqForm.sort_order = 0
}

function editFaq(faq) {
  faqEditingId.value = faq.id || faq.faq_id || ''
  faqForm.category = faq.category || 'service'
  faqForm.question = faq.question || ''
  faqForm.answer = faq.answer || ''
  faqForm.keywords = Array.isArray(faq.keywords) ? faq.keywords.join(', ') : (faq.keywords || '')
  faqForm.active = faq.active !== false
  faqForm.sort_order = Number(faq.sort_order || 0)
}

async function loadFaqs() {
  faqsBusy.value = true
  faqsError.value = ''
  try {
    faqs.value = await listFaqs(password.value)
  } catch (error) {
    faqsError.value = errorMessage(error)
  } finally {
    faqsBusy.value = false
  }
}

async function submitFaq() {
  if (!faqForm.question.trim() || !faqForm.answer.trim()) {
    faqsError.value = '问题与答案不能为空'
    return
  }
  const payload = {
    category: faqForm.category.trim() || 'service',
    question: faqForm.question.trim(),
    answer: faqForm.answer.trim(),
    keywords: faqForm.keywords.split(',').map((item) => item.trim()).filter(Boolean),
    active: faqForm.active,
    sort_order: Number(faqForm.sort_order || 0),
  }
  try {
    if (faqEditingId.value) {
      await updateFaq(faqEditingId.value, payload, password.value)
    } else {
      await createFaq(payload, password.value)
    }
    resetFaqForm()
    await loadFaqs()
  } catch (error) {
    faqsError.value = `FAQ 保存失败：${errorMessage(error)}`
  }
}

async function toggleFaqRecord(faq) {
  try {
    await toggleFaq(faq.id || faq.faq_id, !faq.active, password.value)
    await loadFaqs()
  } catch (error) {
    faqsError.value = `FAQ 启停失败：${errorMessage(error)}`
  }
}

async function removeFaq(faq) {
  if (!window.confirm('确认删除该 FAQ？')) return
  try {
    await deleteFaq(faq.id || faq.faq_id, password.value)
    await loadFaqs()
  } catch (error) {
    faqsError.value = `FAQ 删除失败：${errorMessage(error)}`
  }
}

async function loadDebug() {
  debugBusy.value = true
  debugError.value = ''
  try {
    metrics.value = await getAdminMetrics(password.value)
  } catch (error) {
    metrics.value = null
    debugError.value = `后端状态读取失败：${errorMessage(error)}`
  }

  try {
    const skills = await getSkills(password.value)
    optionalDebug.skills = typeof skills === 'string' ? { available: false, message: '未配置运行时调试代理' } : { available: true, data: skills }
  } catch (error) {
    optionalDebug.skills = { available: false, message: errorMessage(error) }
  }

  try {
    const trace = await getTraceTools(password.value)
    optionalDebug.trace = typeof trace === 'string' ? { available: false, message: '未配置运行时调试代理' } : { available: true, data: trace }
  } catch (error) {
    optionalDebug.trace = { available: false, message: errorMessage(error) }
  }
  debugBusy.value = false
}

async function runKnowledgeReload() {
  reloadResult.value = null
  try {
    reloadResult.value = await reloadKnowledge(password.value)
    await loadDebug()
  } catch (error) {
    reloadResult.value = { success: false, error: errorMessage(error) }
  }
}
</script>

<template>
  <div class="staff-page">
    <header class="site-header">
      <div class="brand">
        <span class="brand-mark">L</span>
        <span>
          <strong>LawMind</strong>
          <small>工作人员控制台</small>
        </span>
      </div>
      <RouterLink class="back-link" to="/">返回客户咨询页</RouterLink>
    </header>

    <main>
      <section v-if="!authenticated" class="login-gate">
        <div class="login-card">
          <p class="eyebrow">STAFF CONSOLE</p>
          <h1>工作人员登录</h1>
          <p class="muted">请输入管理员密码进入咨询记录、律师、FAQ 与知识库管理。</p>
          <form @submit.prevent="login">
            <label>
              <span>管理员密码</span>
              <input v-model="password" type="password" autocomplete="current-password" placeholder="请输入密码" />
            </label>
            <p v-if="loginError" class="form-error" role="alert">{{ loginError }}</p>
            <button type="submit" :disabled="loginBusy">{{ loginBusy ? '验证中…' : '进入控制台' }}</button>
          </form>
        </div>
      </section>

      <section v-else class="console">
        <div class="console-top">
          <div class="tabs" role="tablist">
            <button
              v-for="tab in tabs"
              :key="tab.key"
              :class="{ active: activeTab === tab.key }"
              type="button"
              @click="activeTab = tab.key"
            >
              {{ tab.label }}
            </button>
          </div>
          <button class="quiet-button" type="button" @click="logout">退出</button>
        </div>

        <section v-if="activeTab === 'consultations'" class="tab-panel">
          <div class="panel-heading">
            <h2>咨询记录</h2>
            <button class="quiet-button" type="button" :disabled="recordsBusy" @click="loadConsultations">刷新</button>
          </div>
          <p class="muted">列表中姓名与手机号默认脱敏，点击“查看详情”可看到完整联系方式。</p>
          <p v-if="recordsError" class="form-error" role="alert">{{ recordsError }}</p>

          <div class="table-card">
            <table class="data-table">
              <thead>
                <tr>
                  <th>编号</th>
                  <th>姓名</th>
                  <th>手机号</th>
                  <th>领域</th>
                  <th>状态</th>
                  <th>创建时间</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                <tr v-if="recordsBusy"><td colspan="7" class="empty-cell">加载中…</td></tr>
                <tr v-else-if="records.length === 0"><td colspan="7" class="empty-cell">暂无咨询记录</td></tr>
                <tr v-for="record in records" v-else :key="record.id">
                  <td class="mono">{{ record.id }}</td>
                  <td>{{ record.name || '-' }}</td>
                  <td>{{ record.phone || '-' }}</td>
                  <td>{{ formatDomain(record.legal_domain) }}</td>
                  <td><span :class="['status-pill', statusClass(record.status)]">{{ statusLabels[record.status] || record.status || '-' }}</span></td>
                  <td>{{ record.created_at || '-' }}</td>
                  <td><button class="text-button" type="button" @click="openRecord(record)">查看详情</button></td>
                </tr>
              </tbody>
            </table>
          </div>

          <div v-if="selectedRecord" class="detail-card">
            <div class="detail-heading">
              <h3>咨询详情</h3>
              <button class="quiet-button" type="button" @click="selectedRecord = null">关闭</button>
            </div>
            <dl class="detail-grid">
              <div><dt>编号</dt><dd>{{ selectedRecord.id || '-' }}</dd></div>
              <div><dt>姓名</dt><dd>{{ selectedRecord.contact_name || selectedRecord.name || '-' }}</dd></div>
              <div><dt>手机号</dt><dd>{{ selectedRecord.contact_phone || selectedRecord.phone || '-' }}</dd></div>
              <div><dt>城市</dt><dd>{{ selectedRecord.city || '-' }}</dd></div>
              <div><dt>希望联系时间</dt><dd>{{ selectedRecord.preferred_time || '-' }}</dd></div>
              <div><dt>领域</dt><dd>{{ formatDomain(selectedRecord.legal_domain) }}</dd></div>
              <div><dt>案件阶段</dt><dd>{{ selectedRecord.case_stage || '-' }}</dd></div>
              <div><dt>风险信号</dt><dd>{{ formatValue(selectedRecord.risk_flags) }}</dd></div>
              <div><dt>来源</dt><dd>{{ selectedRecord.source || '-' }}</dd></div>
              <div><dt>创建时间</dt><dd>{{ selectedRecord.created_at || '-' }}</dd></div>
            </dl>
            <div class="detail-actions">
              <label>
                <span>更新状态</span>
                <select v-model="detailStatus" @change="handleStatusChange(selectedRecord)">
                  <option v-for="(label, value) in statusLabels" :key="value" :value="value">{{ label }}</option>
                </select>
              </label>
              <button class="danger-button" type="button" @click="removeSelectedRecord">删除记录</button>
            </div>
          </div>
        </section>

        <section v-if="activeTab === 'lawyers'" class="tab-panel">
          <div class="panel-heading">
            <h2>律师管理</h2>
            <button class="quiet-button" type="button" :disabled="lawyersBusy" @click="loadLawyers">刷新</button>
          </div>
          <p v-if="lawyersError" class="form-error" role="alert">{{ lawyersError }}</p>

          <div class="manager-grid">
            <form class="manager-form" @submit.prevent="submitLawyer">
              <h3>{{ lawyerEditingId ? '编辑律师' : '新增律师' }}</h3>
              <div class="compact-grid">
                <label><span>姓名</span><input v-model="lawyerForm.name" type="text" required /></label>
                <label><span>领域</span><input v-model="lawyerForm.domain" type="text" placeholder="例如: criminal_defense" /></label>
                <label><span>擅长领域</span><input v-model="lawyerForm.specialties" type="text" placeholder="用逗号分隔" /></label>
                <label><span>排序</span><input v-model.number="lawyerForm.sort_order" type="number" /></label>
                <label><span>电话</span><input v-model="lawyerForm.phone" type="text" /></label>
                <label><span>微信</span><input v-model="lawyerForm.wechat" type="text" /></label>
                <label><span>邮箱</span><input v-model="lawyerForm.email" type="email" /></label>
                <label class="checkbox-label"><input v-model="lawyerForm.active" type="checkbox" /><span>启用</span></label>
              </div>
              <label class="full-label"><span>简介</span><textarea v-model="lawyerForm.intro" rows="3"></textarea></label>
              <div class="form-actions">
                <button type="submit">{{ lawyerEditingId ? '保存修改' : '新增律师' }}</button>
                <button v-if="lawyerEditingId" class="quiet-button" type="button" @click="resetLawyerForm">取消编辑</button>
              </div>
            </form>

            <div class="table-card">
              <table class="data-table">
                <thead>
                  <tr><th>姓名</th><th>领域</th><th>联系方式</th><th>状态</th><th>排序</th><th></th></tr>
                </thead>
                <tbody>
                  <tr v-if="lawyersBusy"><td colspan="6" class="empty-cell">加载中…</td></tr>
                  <tr v-else-if="lawyers.length === 0"><td colspan="6" class="empty-cell">暂无律师</td></tr>
                  <tr v-for="lawyer in lawyers" v-else :key="lawyer.id">
                    <td>{{ lawyer.name || '-' }}</td>
                    <td>{{ formatDomain(lawyer.domain) }}</td>
                    <td>{{ lawyer.phone || '-' }}<br /><small>{{ lawyer.email || '' }}</small></td>
                    <td><span class="status-pill" :class="lawyer.active ? 'status-active' : 'status-inactive'">{{ lawyer.active ? '启用' : '停用' }}</span></td>
                    <td>{{ lawyer.sort_order ?? '-' }}</td>
                    <td class="row-actions">
                      <button class="text-button" type="button" @click="editLawyer(lawyer)">编辑</button>
                      <button class="text-button" type="button" @click="toggleLawyerRecord(lawyer)">{{ lawyer.active ? '停用' : '启用' }}</button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </section>

        <section v-if="activeTab === 'faqs'" class="tab-panel">
          <div class="panel-heading">
            <h2>FAQ 管理</h2>
            <button class="quiet-button" type="button" :disabled="faqsBusy" @click="loadFaqs">刷新</button>
          </div>
          <p v-if="faqsError" class="form-error" role="alert">{{ faqsError }}</p>

          <div class="manager-grid">
            <form class="manager-form" @submit.prevent="submitFaq">
              <h3>{{ faqEditingId ? '编辑 FAQ' : '新增 FAQ' }}</h3>
              <div class="compact-grid">
                <label><span>分类</span><input v-model="faqForm.category" type="text" /></label>
                <label><span>关键词</span><input v-model="faqForm.keywords" type="text" placeholder="用逗号分隔" /></label>
                <label><span>排序</span><input v-model.number="faqForm.sort_order" type="number" /></label>
                <label class="checkbox-label"><input v-model="faqForm.active" type="checkbox" /><span>启用</span></label>
              </div>
              <label class="full-label"><span>问题</span><textarea v-model="faqForm.question" rows="2" required></textarea></label>
              <label class="full-label"><span>答案</span><textarea v-model="faqForm.answer" rows="5" required></textarea></label>
              <div class="form-actions">
                <button type="submit">{{ faqEditingId ? '保存修改' : '新增 FAQ' }}</button>
                <button v-if="faqEditingId" class="quiet-button" type="button" @click="resetFaqForm">取消编辑</button>
              </div>
            </form>

            <div class="faq-list">
              <article v-for="faq in faqs" :key="faq.id || faq.faq_id" class="faq-item">
                <div class="faq-item-head">
                  <span :class="['status-pill', faq.active ? 'status-active' : 'status-inactive']">{{ faq.active ? '启用' : '停用' }}</span>
                  <small>{{ faq.category || 'service' }}</small>
                </div>
                <h4>{{ faq.question || '-' }}</h4>
                <p>{{ faq.answer || '-' }}</p>
                <small v-if="faq.keywords?.length" class="faq-keywords">{{ faq.keywords.join(' · ') }}</small>
                <div class="row-actions">
                  <button class="text-button" type="button" @click="editFaq(faq)">编辑</button>
                  <button class="text-button" type="button" @click="toggleFaqRecord(faq)">{{ faq.active ? '停用' : '启用' }}</button>
                  <button class="danger-text-button" type="button" @click="removeFaq(faq)">删除</button>
                </div>
              </article>
              <p v-if="!faqsBusy && faqs.length === 0" class="empty-cell">暂无 FAQ</p>
            </div>
          </div>
        </section>

        <section v-if="activeTab === 'debug'" class="tab-panel">
          <div class="panel-heading">
            <h2>知识库 / 调试</h2>
            <div class="heading-actions">
              <button class="quiet-button" type="button" :disabled="debugBusy" @click="loadDebug">刷新状态</button>
              <button type="button" :disabled="debugBusy" @click="runKnowledgeReload">同步知识库</button>
            </div>
          </div>
          <p class="muted">管理端可读取咨询、律师、FAQ 统计；原版 /skills 与 /trace/tools 仅在部署实际暴露时可用。</p>
          <p v-if="debugError" class="form-error" role="alert">{{ debugError }}</p>

          <div v-if="metrics" class="metrics-grid">
            <div class="metric-card"><span>咨询总数</span><strong>{{ metrics.total_consultations ?? metrics.consultations?.total ?? '-' }}</strong></div>
            <div class="metric-card"><span>待联系</span><strong>{{ metrics.pending_consultations ?? metrics.consultations?.pending ?? '-' }}</strong></div>
            <div class="metric-card"><span>启用律师</span><strong>{{ metrics.active_lawyers ?? metrics.lawyers?.active ?? '-' }}</strong></div>
            <div class="metric-card"><span>启用 FAQ</span><strong>{{ metrics.active_faqs ?? metrics.faqs?.active ?? '-' }}</strong></div>
          </div>

          <div v-if="reloadResult" class="reload-result">
            <strong>同步结果：{{ reloadResult.success ? '成功' : '失败' }}</strong>
            <span v-if="reloadResult.synced !== undefined">同步 {{ reloadResult.synced }} 条，失败 {{ reloadResult.failed }} 条</span>
            <pre>{{ JSON.stringify(reloadResult, null, 2) }}</pre>
          </div>

          <div class="debug-card">
            <h3>调试数据</h3>
            <pre>{{ JSON.stringify(debugSummary, null, 2) }}</pre>
          </div>
        </section>
      </section>
    </main>

    <footer>
      <span>LawMind 工作人员控制台</span>
      <span>管理员密码仅用于当前浏览器会话</span>
    </footer>
  </div>
</template>

<style scoped>
.staff-page {
  width: min(1220px, calc(100% - 32px));
  margin: 0 auto;
  padding: 24px 0 48px;
}
.site-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding-bottom: 20px;
}
.brand {
  display: flex;
  align-items: center;
  gap: 12px;
}
.brand-mark {
  display: grid;
  place-items: center;
  width: 42px;
  height: 42px;
  border-radius: 12px;
  background: linear-gradient(180deg, #6f78f7, #5960dc);
  color: #fff;
  font-weight: 900;
  font-size: 18px;
}
.brand strong,
.brand small {
  display: block;
}
.brand small {
  color: var(--muted);
  margin-top: 2px;
}
.back-link {
  color: var(--text-soft);
  font-size: 14px;
  text-decoration: none;
  padding: 9px 12px;
  border: 1px solid var(--line);
  border-radius: 10px;
}
.back-link:hover {
  color: var(--text);
  border-color: var(--line-strong);
}
.login-gate {
  display: grid;
  place-items: center;
  min-height: 60vh;
}
.login-card {
  width: min(460px, 100%);
  padding: 30px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--panel);
  box-shadow: var(--shadow-low);
}
.login-card h1 {
  margin: 4px 0 10px;
  font-size: 28px;
}
.eyebrow {
  color: var(--accent);
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0.12em;
  margin: 0 0 8px;
}
.muted {
  color: var(--muted);
  font-size: 13px;
  line-height: 1.6;
}
.login-card button {
  margin-top: 18px;
}
.console {
  display: grid;
  gap: 20px;
}
.console-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  flex-wrap: wrap;
}
.tabs {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.tabs button {
  background: transparent;
  border: 1px solid var(--line);
  color: var(--text-soft);
  font-weight: 700;
}
.tabs button.active {
  background: var(--accent-soft);
  border-color: rgba(111, 120, 247, 0.4);
  color: var(--text);
}
.tab-panel {
  display: grid;
  gap: 16px;
}
.panel-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.panel-heading h2 {
  margin: 0;
  font-size: 22px;
}
.heading-actions {
  display: flex;
  gap: 8px;
}
.quiet-button {
  background: transparent;
  border: 1px solid var(--line);
  color: var(--text-soft);
  font-weight: 600;
}
.quiet-button:hover {
  color: var(--text);
  background: var(--panel-2);
}
.danger-button {
  background: transparent;
  border-color: rgba(241, 107, 118, 0.35);
  color: var(--red);
}
.form-error {
  color: var(--red);
  margin: 0;
}
.table-card {
  overflow-x: auto;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--panel);
}
.data-table {
  width: 100%;
  border-collapse: collapse;
  min-width: 760px;
}
.data-table th,
.data-table td {
  padding: 12px 14px;
  border-bottom: 1px solid var(--line);
  text-align: left;
  vertical-align: middle;
  font-size: 14px;
}
.data-table th {
  color: var(--muted);
  font-weight: 700;
  white-space: nowrap;
}
.data-table tbody tr:hover {
  background: rgba(255, 255, 255, 0.02);
}
.data-table small {
  color: var(--muted);
}
.empty-cell {
  padding: 24px;
  color: var(--muted);
  text-align: center;
}
.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  color: var(--text-soft);
}
.status-pill {
  display: inline-block;
  padding: 4px 9px;
  border-radius: 999px;
  background: var(--panel-3);
  color: var(--text-soft);
  font-size: 12px;
  font-weight: 700;
}
.status-pending {
  color: #ffbd6b;
  background: rgba(255, 189, 107, 0.12);
}
.status-contacted {
  color: #8aa4ff;
  background: rgba(138, 164, 255, 0.12);
}
.status-booked {
  color: #41c98c;
  background: rgba(65, 201, 140, 0.12);
}
.status-closed {
  color: var(--muted);
  background: rgba(120, 128, 148, 0.12);
}
.status-active {
  color: #41c98c;
  background: rgba(65, 201, 140, 0.12);
}
.status-inactive {
  color: var(--muted);
}
.text-button {
  min-height: 28px;
  padding: 0;
  background: transparent;
  border-color: transparent;
  color: var(--accent);
  font-size: 13px;
  font-weight: 700;
}
.danger-text-button {
  min-height: 28px;
  padding: 0;
  background: transparent;
  border-color: transparent;
  color: var(--red);
  font-size: 13px;
  font-weight: 700;
}
.detail-card {
  padding: 20px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--panel);
}
.detail-heading {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  margin-bottom: 12px;
}
.detail-heading h3 {
  margin: 0;
}
.detail-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 14px;
  margin: 0;
}
.detail-grid dt {
  color: var(--muted);
  font-size: 12px;
  margin-bottom: 4px;
}
.detail-grid dd {
  margin: 0;
  color: var(--text);
  font-size: 14px;
}
.detail-actions {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 14px;
  flex-wrap: wrap;
  margin-top: 20px;
}
.detail-actions label span {
  display: block;
  color: var(--text-soft);
  font-size: 12px;
  margin-bottom: 6px;
}
select {
  min-width: 180px;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--panel);
  color: var(--text);
}
.manager-grid {
  display: grid;
  grid-template-columns: minmax(300px, 0.8fr) minmax(0, 1.2fr);
  gap: 16px;
  align-items: start;
}
.manager-form {
  padding: 20px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--panel);
}
.manager-form h3 {
  margin: 0 0 16px;
}
.compact-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
label span {
  display: block;
  color: var(--text-soft);
  font-size: 12px;
  margin-bottom: 6px;
}
.checkbox-label {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 24px;
}
.checkbox-label span {
  margin: 0;
}
.checkbox-label input {
  width: 16px;
  height: 16px;
  accent-color: var(--accent);
}
.full-label {
  display: block;
  margin-top: 12px;
}
.form-actions {
  display: flex;
  gap: 8px;
  margin-top: 14px;
}
.row-actions {
  display: flex;
  gap: 10px;
}
.faq-list {
  display: grid;
  gap: 10px;
}
.faq-item {
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--panel);
}
.faq-item-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}
.faq-item-head small {
  color: var(--muted);
}
.faq-item h4 {
  margin: 0 0 6px;
  font-size: 15px;
}
.faq-item p {
  margin: 0 0 8px;
  color: var(--text-soft);
  line-height: 1.6;
  font-size: 14px;
}
.faq-keywords {
  color: var(--muted);
}
.metrics-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px;
}
.metric-card {
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--panel);
}
.metric-card span {
  display: block;
  color: var(--muted);
  font-size: 12px;
  margin-bottom: 8px;
}
.metric-card strong {
  font-size: 28px;
}
.debug-card {
  padding: 18px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--panel);
}
.debug-card h3 {
  margin: 0 0 12px;
}
pre {
  max-height: 480px;
  overflow: auto;
  margin: 0;
  padding: 14px;
  border-radius: 10px;
  background: #0b0c0f;
  color: #d9ddeb;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
}
.reload-result {
  padding: 14px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--panel);
  color: var(--text-soft);
}
.reload-result strong {
  display: block;
  color: var(--text);
  margin-bottom: 4px;
}
footer {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding-top: 28px;
  color: var(--muted);
  font-size: 12px;
}
@media (max-width: 900px) {
  .manager-grid {
    grid-template-columns: 1fr;
  }
  .compact-grid {
    grid-template-columns: 1fr;
  }
}
</style>