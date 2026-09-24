<script setup lang="ts">
import { onMounted, onUnmounted, reactive, ref } from 'vue';
import { api } from '../api';
import type { MySqlInput } from '../types';

const props = defineProps<{ linux: boolean }>();
const form = reactive<MySqlInput>({ username: 'root', port: 3306, password: '' });
const configured = ref(false), busy = ref(false), message = ref(''), error = ref('');
let alive = true;
onUnmounted(() => { alive = false; form.password = ''; });
onMounted(async () => {
  if (!props.linux) return;
  busy.value = true;
  try {
    const status = await api.mysql();
    if (alive) { configured.value = status.configured; form.username = status.username; form.port = status.port; }
  } catch (exception) { if (alive) error.value = exception instanceof Error ? exception.message : '读取MySQL配置失败'; }
  finally { if (alive) busy.value = false; }
});
async function save() {
  if (busy.value || !props.linux) return;
  busy.value = true; error.value = ''; message.value = '';
  try {
    const status = await api.saveMysql({ ...form });
    if (alive) { configured.value = status.configured; message.value = '连接验证通过，后续项目可自动建库和迁移。回到项目点击“发布项目”即可重试。'; }
  } catch (exception) { if (alive) error.value = exception instanceof Error ? exception.message : 'MySQL配置保存失败'; }
  finally { form.password = ''; if (alive) busy.value = false; }
}
</script>

<template>
  <section class="panel configuration">
    <h2>MySQL 自动部署</h2>
    <p>复用宝塔已有 MySQL，一次授权后自动为项目创建数据库和独立账号、写入连接配置，并在发布后端前执行工程提供的迁移。</p>
    <p class="hint">{{ configured ? '服务器已有管理配置，发布时会再次检查连接与实例归属。' : '尚未配置管理凭据；请填写宝塔数据库管理账号，通常为 root。' }}</p>
    <form @submit.prevent="save">
      <div class="form-grid">
        <label>本机 MySQL 端口<input v-model.number="form.port" type="number" min="1" max="65535" required :disabled="busy || !linux"></label>
        <label>管理账号<input v-model="form.username" maxlength="32" autocomplete="off" required :disabled="busy || !linux"></label>
      </div>
      <label>管理密码<input v-model="form.password" type="password" maxlength="4096" autocomplete="new-password" required :disabled="busy || !linux" :placeholder="configured ? '输入新密码以验证并更新配置' : '宝塔 MySQL 的管理密码，不是面板登录密码'"></label>
      <p class="hint">只连接当前服务器的 127.0.0.1。密码仅保存在服务器受保护配置中，不回显、不写入 Git，也不提供给项目代码。新建库和账号重名、已有连接不同或迁移冲突时会提示。</p>
      <p v-if="!linux" class="hint">Windows 预览不连接数据库，请在服务器面板完成一次授权。</p>
      <p v-if="error" class="error" role="alert">{{ error }}</p><p v-if="message" class="callout" role="status">{{ message }}</p>
      <button class="primary" :disabled="busy || !linux">{{ busy ? '正在检查…' : '验证连接并保存' }}</button>
    </form>
  </section>
</template>
