<script setup lang="ts">
import { onMounted, onUnmounted, reactive, ref } from 'vue';
import { api } from '../api';
import { date } from '../format';
import type { HttpsInput, HttpsStatus } from '../types';

const props = defineProps<{ linux: boolean }>();
const emit = defineEmits<{ saved: [] }>();
const form = reactive<HttpsInput>({ domain: 'www.halfsuger.top', certificatePath: '', privateKeyPath: '' });
const status = ref<HttpsStatus>(), busy = ref(false), error = ref(''), message = ref('');
let alive = true;
onUnmounted(() => { alive = false; });
onMounted(async () => {
  if (!props.linux) return;
  busy.value = true;
  try {
    const result = await api.https();
    if (alive) {
      status.value = result;
      if (result.configured) Object.assign(form, { domain: result.domain, certificatePath: result.certificatePath, privateKeyPath: result.privateKeyPath });
    }
  } catch (exception) { if (alive) error.value = exception instanceof Error ? exception.message : '读取HTTPS配置失败'; }
  finally { if (alive) busy.value = false; }
});
async function save() {
  if (busy.value || !props.linux) return;
  busy.value = true; error.value = ''; message.value = '';
  try {
    const result = await api.saveHttps({ domain: form.domain.trim().toLowerCase(), certificatePath: form.certificatePath.trim(), privateKeyPath: form.privateKeyPath.trim() });
    if (alive) { status.value = result; message.value = 'HTTPS已启用。现有和后续发布的前端使用默认域名与原端口访问，项目服务卡片中可打开访问地址。'; emit('saved'); }
  } catch (exception) { if (alive) error.value = exception instanceof Error ? exception.message : 'HTTPS配置失败'; }
  finally { if (alive) busy.value = false; }
}
</script>

<template>
  <section class="panel configuration">
    <h2>默认域名与 HTTPS</h2>
    <p>读取服务器已有的 ACME 证书，为前端站点自动配置 HTTPS。多个项目共用域名，通过各自的前端端口访问。</p>
    <form @submit.prevent="save">
      <label>默认域名<input v-model="form.domain" required maxlength="253" placeholder="www.halfsuger.top" autocomplete="off" :disabled="busy || !linux"></label>
      <label>完整证书链路径<input v-model="form.certificatePath" required maxlength="512" placeholder="/etc/ssl/project/fullchain.pem" autocomplete="off" :disabled="busy || !linux"></label>
      <label>私钥路径<input v-model="form.privateKeyPath" required maxlength="512" placeholder="/etc/ssl/project/private.key" autocomplete="off" :disabled="busy || !linux"></label>
      <p class="hint">填写服务器上的完整文件路径。保存前检查域名、证书有效期和私钥匹配；保存成功后，现有前端端口也会切换为 HTTPS，已停止的服务保持停止。</p>
      <p class="hint">每 15 分钟检查证书文件变化，校验通过后自动更新。证书的申请、续期由服务器原有 ACME 任务负责。</p>
      <dl v-if="status?.configured"><dt>证书到期</dt><dd>{{ date(status.expiresAt) }}</dd><dt>最近应用</dt><dd>{{ date(status.appliedAt) }}</dd><dt>最近检查</dt><dd>{{ date(status.lastCheckedAt) }}</dd></dl>
      <p v-if="status?.syncError" class="error" role="alert">证书同步失败：{{ status.syncError }}</p>
      <p v-if="!linux" class="hint">请在服务器面板配置证书路径，Windows 预览不会读取或安装服务器证书。</p>
      <p v-if="error" class="error" role="alert">{{ error }}</p><p v-if="message" class="callout" role="status">{{ message }}</p>
      <button class="primary" :disabled="busy || !linux">{{ busy ? '正在验证并应用…' : status?.configured ? '验证并更新 HTTPS' : '验证并启用 HTTPS' }}</button>
    </form>
  </section>
</template>
