<script setup>
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { RouterLink } from 'vue-router'
import {
  getOptions,
  getLawyers,
  lawChat,
  saveConsultation,
  transferToHuman,
} from '../lib/lawApi'

const messages = ref([
  {
    role: 'assistant',
    content: '您好，我是智能法律咨询助手。请描述您遇到的法律问题，我会帮您梳理情况并给出初步风险提示。',
  },
])
const draft = ref('')
const chatBusy = ref(false)
const serviceNotice = ref('')
const options = ref({})
const conversationId = ref('')
const sessionToken = ref('')
const currentDomain = ref('')
const caseStage = ref('')
const missingFacts = ref([])
const riskFlags = ref([])
const recommendedLawyers = ref([])
const consultationId = ref('')
const contactBusy = ref(false)
const contactError = ref('')
const submitted = ref(null)
const chatList = ref(null)

const form = reactive({
  name: '',
  phone: '',
  city: '',
  preferred_time: '',
  consent: false,
})

const domainTags = computed(() => (options.value.legal_domain_options || []).slice(0, 8))
const domainLabel = computed(() => {
  const item = domainTags.value.find((option) => option.value === currentDomain.value)
  return item?.label || currentDomain.value || ''
})
const highRisk = computed(() => riskFlags.value.length > 0)
const riskLabels = {
  detention: '已刑事拘留',
  court_soon: '即将开庭',
  injury: '已发生人身伤亡',
  traffic_accident: '发生交通事故',
  filed: '已立案',
  prosecution: '审查起诉阶段',
  no_lawyer: '无律师代理',
}

function errorMessage(error) {
  return error?.detail || error?.message || '请求失败，请稍后重试'
}

async function loadOptions() {
  try {
    options.value = await getOptions()
    serviceNotice.value = ''
  } catch (error) {
    serviceNotice.value = `法律咨询服务暂时不可用：${errorMessage(error)}`
  }
}

async function sendChat(messageText = draft.value) {
  const content = String(messageText || '').trim()
  if (!content || chatBusy.value) return
  draft.value = ''
  messages.value.push({ role: 'user', content })
  chatBusy.value = true
  serviceNotice.value = ''
  try {
    const data = await lawChat(
      { message: content, conversation_id: conversationId.value || undefined },
      sessionToken.value,
    )
    conversationId.value = data.conversation_id || conversationId.value
    sessionToken.value = data.session_token || sessionToken.value
    currentDomain.value = data.legal_domain || currentDomain.value
    caseStage.value = data.case_stage || ''
    missingFacts.value = data.missing_facts || []
    riskFlags.value = data.risk_flags || []
    recommendedLawyers.value = data.recommended_lawyers || []
    messages.value.push({
      role: 'assistant',
      content: data.response || '我已收到您的问题。当前服务暂时无法生成完整分析，请稍后重试。',
    })
  } catch (error) {
    serviceNotice.value = `法律咨询服务暂时不可用：${errorMessage(error)}`
    messages.value.push({
      role: 'assistant',
      content: '抱歉，咨询服务暂时不可用。您可以稍后重试，或直接留下联系方式让我们为您转人工处理。',
    })
  } finally {
    chatBusy.value = false
    await nextTick()
    chatList.value?.scrollTo({ top: chatList.value.scrollHeight, behavior: 'smooth' })
  }
}

function useTag(label) {
  draft.value = label
  sendChat(label)
}

async function loadRecommended(domain = currentDomain.value) {
  if (!domain) return
  try {
    recommendedLawyers.value = await getLawyers(domain)
  } catch {
    recommendedLawyers.value = []
  }
}

function validateContact() {
  if (!form.name.trim()) return '请填写您的称呼'
  if (!/^1[3-9]\d{9}$/.test(form.phone.trim())) return '请填写正确的中国大陆手机号'
  if (!form.consent) return '请先同意工作人员与您联系'
  return ''
}

function contactPayload() {
  return {
    conversation_id: conversationId.value || undefined,
    session_token: sessionToken.value || undefined,
    name: form.name.trim(),
    phone: form.phone.trim(),
    city: form.city.trim() || undefined,
    preferred_time: form.preferred_time.trim() || undefined,
    consent: form.consent,
    legal_domain: currentDomain.value || undefined,
  }
}

async function submitConsultation() {
  contactError.value = validateContact()
  if (contactError.value) return
  contactBusy.value = true
  try {
    const data = await saveConsultation(contactPayload(), sessionToken.value)
    submitted.value = {
      id: data.consultation_id,
      message: data.message || '咨询已提交，我们会尽快联系您。',
    }
    consultationId.value = data.consultation_id || consultationId.value
  } catch (error) {
    contactError.value = `提交失败：${errorMessage(error)}`
  } finally {
    contactBusy.value = false
  }
}

async function submitTransfer() {
  contactError.value = validateContact()
  if (contactError.value) return
  contactBusy.value = true
  try {
    const data = await transferToHuman(contactPayload(), sessionToken.value)
    submitted.value = {
      id: data.consultation_id,
      message: data.message || '已收到您的转人工请求，工作人员将尽快与您联系。',
      transferred: true,
    }
    consultationId.value = data.consultation_id || consultationId.value
  } catch (error) {
    contactError.value = `转人工失败：${errorMessage(error)}`
  } finally {
    contactBusy.value = false
  }
}

function resetSubmission() {
  submitted.value = null
  contactError.value = ''
  form.name = ''
  form.phone = ''
  form.city = ''
  form.preferred_time = ''
  form.consent = false
}

onMounted(async () => {
  await loadOptions()
  await loadRecommended()
})
</script>

<template>
  <div class="customer-page">
    <header class="site-header">
      <div class="brand">
        <span class="brand-mark">L</span>
        <span>
          <strong>LawMind</strong>
          <small>智能法律咨询助手</small>
        </span>
      </div>
      <RouterLink class="staff-link" to="/staff">工作人员入口</RouterLink>
    </header>

    <main>
      <section class="hero">
        <div>
          <p class="eyebrow">LAWMIND · 智能法律咨询</p>
          <h1>描述您的法律问题</h1>
          <p class="hero-copy">
            7 类常见个人法律问题均可咨询。AI 会先帮您梳理案情、提示缺失关键事实，并根据风险情况建议是否转人工。
          </p>
        </div>
        <div v-if="domainTags.length" class="domain-tags" aria-label="法律领域快捷入口">
          <button
            v-for="tag in domainTags"
            :key="tag.value"
            type="button"
            class="domain-tag"
            @click="useTag(`我想咨询${tag.label}问题`)"
          >
            {{ tag.label }}
          </button>
        </div>
      </section>

      <div v-if="serviceNotice" class="service-alert" role="alert">
        <strong>服务暂时不可用</strong>
        <span>{{ serviceNotice }}</span>
        <button type="button" @click="loadOptions">重新检测</button>
      </div>

      <div class="layout">
        <section class="chat-card">
          <div class="chat-head">
            <div>
              <strong>在线法律咨询</strong>
              <small v-if="domainLabel">当前领域：{{ domainLabel }}</small>
              <small v-else>请描述你的情况</small>
            </div>
            <span v-if="chatBusy" class="typing">正在分析…</span>
          </div>

          <div ref="chatList" class="messages" aria-live="polite">
            <article
              v-for="(message, index) in messages"
              :key="`${message.role}-${index}`"
              :class="['message', message.role]"
            >
              <small>{{ message.role === 'user' ? '您' : '助手' }}</small>
              <p>{{ message.content }}</p>
            </article>
            <article v-if="chatBusy" class="message assistant">
              <small>助手</small>
              <p>正在整理回答…</p>
            </article>
          </div>

          <form class="composer" @submit.prevent="sendChat()">
            <textarea
              v-model="draft"
              rows="3"
              placeholder="例如：我老公因为醉驾被带走，现在该怎么办？"
              @keydown.enter.exact.prevent="sendChat()"
            ></textarea>
            <div class="composer-actions">
              <span>Enter 发送，Shift + Enter 换行</span>
              <button type="submit" :disabled="chatBusy || !draft.trim()">
                {{ chatBusy ? '分析中' : '发送' }}
              </button>
            </div>
          </form>
        </section>

        <aside class="insights">
          <section v-if="missingFacts.length" class="card facts-card">
            <h2>还需要补充的关键信息</h2>
            <div class="chip-list">
              <span v-for="fact in missingFacts" :key="fact" class="chip">{{ fact }}</span>
            </div>
          </section>

          <section v-if="caseStage || riskFlags.length" class="card risk-card">
            <h2>初步风险分析</h2>
            <p v-if="caseStage" class="stage">当前阶段：{{ caseStage }}</p>
            <ul v-if="riskFlags.length" class="risk-list">
              <li v-for="flag in riskFlags" :key="flag">{{ riskLabels[flag] || flag }}</li>
            </ul>
            <p v-if="highRisk" class="risk-warning">
              以上信息可能涉及紧急或较高风险，请尽快留下联系方式申请人工介入。
            </p>
            <p class="muted">以上仅为基于当前信息的初步提示，不构成正式法律意见。</p>
          </section>

          <section class="card disclaimer-card">
            <h2>免责声明</h2>
            <p>
              本页提供的咨询和建议仅为一般性法律信息与初步风险梳理，不构成正式法律意见，也不替代执业律师对具体案件的判断。
            </p>
          </section>
        </aside>
      </div>

      <section v-if="recommendedLawyers.length" class="lawyer-section">
        <div class="section-title">
          <h2>为您推荐</h2>
          <small>仅展示公开信息</small>
        </div>
        <div class="lawyer-grid">
          <article v-for="lawyer in recommendedLawyers" :key="lawyer.id || lawyer.name" class="lawyer-card">
            <div class="lawyer-avatar">{{ (lawyer.name || '律').slice(0, 1) }}</div>
            <h3>{{ lawyer.name }}</h3>
            <p v-if="lawyer.specialties?.length" class="specialties">
              {{ lawyer.specialties.join(' · ') }}
            </p>
            <p v-if="lawyer.intro" class="intro">{{ lawyer.intro }}</p>
          </article>
        </div>
      </section>

      <section class="contact-section card">
        <div class="section-title">
          <h2>{{ consultationId ? '更新联系方式' : '留下联系方式' }}</h2>
          <small>提交后由律所工作人员进一步联系</small>
        </div>

        <div v-if="submitted" class="submitted" role="status">
          <strong>{{ submitted.message }}</strong>
          <span v-if="submitted.id">咨询编号：{{ submitted.id }}</span>
          <button type="button" class="link-button" @click="resetSubmission">继续填写</button>
        </div>

        <form v-else class="contact-form" @submit.prevent="submitConsultation">
          <div class="form-grid">
            <label>
              <span>称呼</span>
              <input v-model="form.name" type="text" autocomplete="name" placeholder="姓名或称呼" required />
            </label>
            <label>
              <span>手机号</span>
              <input v-model="form.phone" type="tel" autocomplete="tel" placeholder="用于工作人员联系您" required />
            </label>
            <label>
              <span>所在城市</span>
              <input v-model="form.city" type="text" autocomplete="address-level2" placeholder="例如：上海" />
            </label>
            <label>
              <span>希望联系时间</span>
              <input v-model="form.preferred_time" type="text" placeholder="例如：工作日 19:00 后" />
            </label>
          </div>

          <label class="consent-line">
            <input v-model="form.consent" type="checkbox" />
            <span>我同意律所工作人员通过上述电话与我联系，并同意仅用于本次法律咨询沟通。</span>
          </label>

          <p v-if="contactError" class="form-error" role="alert">{{ contactError }}</p>

          <div class="form-actions">
            <button type="submit" :disabled="contactBusy">
              {{ contactBusy ? '提交中…' : '提交咨询' }}
            </button>
            <button
              type="button"
              class="secondary-button"
              :disabled="contactBusy"
              @click="submitTransfer"
            >
              {{ contactBusy ? '提交中…' : '转人工' }}
            </button>
          </div>
          <p class="muted disclaimer-small">提交后，工作人员会根据您提供的信息与您联系。请勿在对话中填写身份证号、银行卡号等敏感信息。</p>
        </form>
      </section>
    </main>

    <footer>
      <span>LawMind</span>
      <span>本页面不展示内部 Agent / RAG / 工具运行信息</span>
    </footer>
  </div>
</template>

<style scoped>
.customer-page {
  width: min(1200px, calc(100% - 32px));
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
.staff-link {
  color: var(--text-soft);
  text-decoration: none;
  font-size: 14px;
  padding: 9px 12px;
  border: 1px solid var(--line);
  border-radius: 10px;
}
.staff-link:hover {
  color: var(--text);
  border-color: var(--line-strong);
}
.hero {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  padding: 28px 0 18px;
}
.eyebrow {
  margin: 0 0 8px;
  color: var(--accent);
  font-size: 12px;
  letter-spacing: 0.12em;
  font-weight: 800;
}
h1,
h2,
h3,
p {
  margin-top: 0;
}
h1 {
  margin-bottom: 10px;
  font-size: clamp(26px, 4vw, 40px);
}
.hero-copy {
  max-width: 640px;
  color: var(--text-soft);
  line-height: 1.7;
}
.domain-tags {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
  max-width: 430px;
}
.domain-tag {
  min-height: 36px;
  padding: 0 12px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: var(--panel);
  color: var(--text-soft);
  font-size: 13px;
  font-weight: 600;
}
.domain-tag:hover {
  color: var(--text);
  border-color: var(--accent);
}
.service-alert {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  padding: 12px 14px;
  border: 1px solid rgba(241, 107, 118, 0.35);
  border-radius: 12px;
  background: var(--red-soft);
  color: var(--text);
  margin-bottom: 18px;
}
.service-alert button {
  margin-left: auto;
  min-height: 32px;
  padding: 0 10px;
  background: transparent;
  border-color: rgba(241, 107, 118, 0.4);
  color: var(--text-soft);
  font-size: 12px;
}
.layout {
  display: grid;
  grid-template-columns: minmax(0, 1.5fr) minmax(300px, 0.8fr);
  gap: 18px;
  align-items: start;
}
.chat-card {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  overflow: hidden;
  box-shadow: var(--shadow-low);
}
.chat-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 16px 18px;
  border-bottom: 1px solid var(--line);
}
.chat-head small {
  display: block;
  margin-top: 4px;
  color: var(--muted);
}
.typing {
  color: var(--accent);
  font-size: 13px;
}
.messages {
  height: 420px;
  overflow-y: auto;
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.message {
  max-width: 86%;
  padding: 12px 14px;
  border-radius: 14px;
  background: var(--panel-2);
  border: 1px solid var(--line);
}
.message.user {
  align-self: flex-end;
  background: var(--accent-soft);
  border-color: rgba(111, 120, 247, 0.25);
}
.message small {
  display: block;
  margin-bottom: 6px;
  color: var(--muted);
  font-size: 12px;
}
.message p {
  margin: 0;
  color: var(--text);
  line-height: 1.65;
  white-space: pre-wrap;
}
.composer {
  border-top: 1px solid var(--line);
  padding: 14px 18px 16px;
}
.composer-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 10px;
  color: var(--muted);
  font-size: 12px;
}
.insights {
  display: grid;
  gap: 14px;
}
.card {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  padding: 20px;
}
.card h2,
.lawyer-section h2,
.contact-section h2 {
  margin-bottom: 8px;
  font-size: 18px;
}
.chip-list,
.risk-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 0;
  margin: 10px 0 0;
  list-style: none;
}
.chip,
.risk-list li {
  padding: 6px 10px;
  border-radius: 8px;
  background: var(--panel-3);
  color: var(--text-soft);
  font-size: 13px;
}
.risk-card {
  border-color: rgba(241, 107, 118, 0.25);
}
.risk-warning {
  margin: 14px 0 8px;
  color: var(--red);
  font-weight: 700;
}
.stage {
  color: var(--text-soft);
  margin-bottom: 6px;
}
.muted {
  color: var(--muted);
  font-size: 13px;
  line-height: 1.6;
}
.disclaimer-card {
  color: var(--text-soft);
}
.lawyer-section,
.contact-section {
  margin-top: 24px;
}
.section-title {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}
.section-title small {
  color: var(--muted);
}
.lawyer-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
  gap: 14px;
}
.lawyer-card {
  padding: 18px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--panel);
}
.lawyer-avatar {
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  border-radius: 12px;
  background: var(--accent-soft);
  color: var(--accent);
  font-weight: 900;
  margin-bottom: 12px;
}
.lawyer-card h3 {
  margin-bottom: 6px;
}
.specialties {
  color: var(--accent);
  font-size: 13px;
  margin-bottom: 8px;
}
.intro {
  color: var(--text-soft);
  font-size: 14px;
  line-height: 1.6;
}
.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}
label span {
  display: block;
  margin-bottom: 6px;
  color: var(--text-soft);
  font-size: 13px;
}
.consent-line {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin-top: 16px;
  color: var(--text-soft);
  font-size: 14px;
  cursor: pointer;
}
.consent-line input {
  width: 16px;
  height: 16px;
  margin-top: 2px;
  accent-color: var(--accent);
}
.form-error {
  color: var(--red);
  margin: 12px 0 0;
}
.form-actions {
  display: flex;
  gap: 10px;
  margin-top: 16px;
}
.secondary-button {
  background: transparent;
  border: 1px solid var(--line-strong);
  color: var(--text-soft);
}
.secondary-button:hover {
  color: var(--text);
  background: var(--panel-2);
}
.disclaimer-small {
  margin: 16px 0 0;
}
.submitted {
  padding: 16px;
  border-radius: 12px;
  background: var(--green-soft);
  border: 1px solid rgba(65, 201, 140, 0.3);
  color: var(--green);
  display: grid;
  gap: 6px;
}
.link-button {
  width: fit-content;
  min-height: 32px;
  background: transparent;
  border-color: transparent;
  color: var(--accent);
  padding: 0;
}
footer {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding-top: 28px;
  color: var(--muted);
  font-size: 12px;
}
@media (max-width: 860px) {
  .hero,
  .layout {
    display: block;
  }
  .domain-tags {
    justify-content: flex-start;
    margin-top: 16px;
  }
  .insights {
    margin-top: 14px;
  }
  .form-grid {
    grid-template-columns: 1fr;
  }
}
</style>