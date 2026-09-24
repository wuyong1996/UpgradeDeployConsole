<script setup lang="ts">
import { computed, onUnmounted, reactive, ref, watch } from 'vue';
import type { GitAuthInput, Project, ProjectInput, RemoteBranch, Target } from '../types';
import { api } from '../api';
import { defaults, projectSlug, updateDefaultPaths } from '../format';
const props = defineProps<{ project?: Project; slug: string; usedSlugs: string[]; targets: Target[]; busy: boolean; error: string }>();
const emit = defineEmits<{ close: []; save: [input: ProjectInput] }>();
const form = reactive<ProjectInput>(props.project ? { version: props.project.version, slug: props.project.slug, name: props.project.name, environment: props.project.environment, repository: props.project.repository, branch: props.project.branch, frontPath: props.project.frontPath, backPath: props.project.backPath, repositoryPath: props.project.repositoryPath } : { version: 0, slug: props.slug, name: '', environment: 'test', repository: '', branch: '', ...defaults(props.slug) });
const slugEdited = ref(false);
const deployAfterSave = ref(!props.project?.shutdownRequested && !(props.project?.front.stopped && props.project?.back.stopped));
const enableSchedule = ref(!props.project || props.project.front.autoDeploy || props.project.back.autoDeploy);
const auth = reactive<GitAuthInput>({ mode: props.project?.gitAuthMode ?? 'none', username: props.project?.gitUsername ?? '', secret: '', useStored: props.project?.gitAuthMode === 'https' });
const branches = ref<RemoteBranch[]>([]), fetching = ref(false), branchError = ref(''), verification = ref('');
const ssh = computed(() => form.repository.startsWith('ssh://'));
const canUseStored = computed(() => props.project?.gitAuthMode === 'https' && props.project.repository === form.repository && props.project.gitUsername === auth.username);
const ready = computed(() => !!verification.value && branches.value.some(branch => branch.name === form.branch));
let generation = 0, alive = true;
watch([() => form.repository, () => form.slug, () => auth.mode, () => auth.username, () => auth.secret, () => auth.useStored], () => {
  generation++; fetching.value = false; verification.value = ''; branches.value = []; form.branch = ''; branchError.value = '';
  if (!canUseStored.value) auth.useStored = false;
}, { flush: 'sync' });
watch(ssh, value => { if (value) { auth.mode = 'none'; auth.secret = ''; auth.useStored = false; } });
function authInput(): GitAuthInput { return auth.mode === 'none' ? { mode: 'none', username: '', useStored: false } : { ...auth, secret: auth.useStored ? undefined : auth.secret }; }
async function fetchBranches() {
  if (fetching.value || props.busy) return;
  form.repository = form.repository.trim();
  const request = ++generation;
  const previous = form.branch || (form.repository === props.project?.repository ? props.project.branch : '');
  fetching.value = true; branchError.value = ''; verification.value = ''; branches.value = []; form.branch = '';
  try {
    const result = await api.branches({ repository: form.repository, slug: form.slug, projectId: props.project?.id, gitAuth: authInput() });
    if (!alive || request !== generation) return;
    branches.value = result.branches; verification.value = result.verification;
    form.branch = [previous, result.defaultBranch, result.branches[0]?.name].find(name => name && result.branches.some(branch => branch.name === name)) || '';
    if (!result.branches.length) branchError.value = '仓库没有可选分支，请先向仓库提交代码。';
  } catch (error) { if (alive && request === generation) branchError.value = error instanceof Error ? error.message : '获取分支失败，请重试'; }
  finally { if (alive && request === generation) fetching.value = false; }
}
function save() { if (ready.value && !props.busy && !fetching.value) emit('save', { ...form, gitAuth: authInput(), branchVerification: verification.value, deployAfterSave: deployAfterSave.value, enableSchedule: deployAfterSave.value && enableSchedule.value }); }
onUnmounted(() => { alive = false; generation++; auth.secret = ''; });
function generateSlug() {
  if (props.project || props.busy || !form.name.trim() || (slugEdited.value && form.slug.trim())) return;
  form.slug = projectSlug(form.name, [...props.usedSlugs, ...props.targets.map(target => target.slug)]);
  slugEdited.value = false;
}
watch(() => form.slug, (value, old) => { updateDefaultPaths(form, old); const target = props.targets.find(x => x.slug === value); if (target && !props.project) { form.frontPath = target.front?.current || defaults(value).frontPath; form.backPath = target.back?.current || defaults(value).backPath; form.repositoryPath = target.repositoryPath; } });
</script>
<template>
  <div class="overlay"><section class="modal" role="dialog" aria-modal="true" aria-labelledby="form-title">
    <header class="section-heading"><div><span class="eyebrow">PROJECT CONFIGURATION</span><h2 id="form-title">{{ project ? '编辑项目配置' : '添加项目' }}</h2></div><button class="icon-button" aria-label="关闭" :disabled="busy" @click="emit('close')">×</button></header>
    <form @submit.prevent="save">
      <div class="form-grid"><label>项目名称<input v-model="form.name" required maxlength="100" placeholder="例如：业务管理平台" autofocus @blur="generateSlug"></label><label>项目标识<input v-model="form.slug" :disabled="!!project" required pattern="[a-z][a-z0-9-]{0,63}" list="target-slugs" @input="slugEdited = true"><datalist id="target-slugs"><option v-for="target in targets" :key="target.slug" :value="target.slug" /></datalist><small v-if="!project" class="hint">名称填写后自动生成，可手动修改。</small></label>
      <label>环境<select v-model="form.environment"><option value="test">测试环境</option><option value="production">正式环境</option></select></label></div>
      <label>Git 地址<input v-model="form.repository" required maxlength="2000" placeholder="https://git.example.com/team/project.git"></label>
      <label v-if="!ssh">Git 认证<select v-model="auth.mode"><option value="none">公开仓库 · 无需认证</option><option value="https">用户名 + 密码 / Token</option></select></label>
      <template v-if="auth.mode === 'https' && !ssh">
        <label>Git 用户名<input v-model="auth.username" required maxlength="200" autocomplete="off" placeholder="Git 账号用户名"></label>
        <label v-if="canUseStored">已保存凭据<select v-model="auth.useStored"><option :value="true">使用服务器已保存的凭据</option><option :value="false">更换密码或 Token</option></select></label>
        <label v-if="!auth.useStored">Git 密码 / Token<input v-model="auth.secret" type="password" required maxlength="4096" autocomplete="off" placeholder="GitHub 请填写 Personal Access Token"></label>
        <p class="hint">GitHub 使用 Token，不能使用账号登录密码。保存后凭据在服务器加密存储，用于自动更新；页面不会回显。</p>
      </template>
      <p v-if="ssh" class="hint">SSH 使用项目构建账号在服务器配置的密钥和 known_hosts；请先完成服务器项目接入。</p>
      <div class="button-row"><button type="button" :disabled="busy || fetching || !form.repository || (auth.mode === 'https' && (!auth.username || (!auth.useStored && !auth.secret)))" @click="fetchBranches">{{ fetching ? '正在获取分支…' : '获取分支' }}</button><span class="hint">{{ ready ? `已获取 ${branches.length} 个分支` : '填写地址和认证信息后获取远端分支' }}</span></div>
      <label>跟踪分支<select v-model="form.branch" required :disabled="!branches.length || fetching"><option disabled value="">{{ fetching ? '正在获取…' : '请先获取分支' }}</option><option v-for="branch in branches" :key="branch.name" :value="branch.name">{{ branch.name }}</option></select></label>
      <p v-if="branchError" class="error" role="alert">{{ branchError }}</p>
      <p class="hint">仓库地址不包含密码或 Token。切换来源会先暂停旧发布计划。</p>
      <div class="divider" /><h3>服务器部署目录</h3><p class="hint">按项目标识推荐默认值，也可以自己填写。首次发布会准备空目录，已有文件不会被覆盖。</p>
      <label>前端本地路径<input v-model="form.frontPath" required maxlength="400"></label><label>后端本地路径<input v-model="form.backPath" required maxlength="400"></label><label>Git 源码路径<input v-model="form.repositoryPath" required maxlength="400"></label>
      <div class="divider" /><h3>保存后的操作</h3>
      <label class="check-option"><input v-model="deployAfterSave" type="checkbox">保存后立即拉取代码并发布</label>
      <label v-if="deployAfterSave" class="check-option"><input v-model="enableSchedule" type="checkbox">发布成功后按检查周期持续自动更新</label>
      <p class="callout">保存后自动拉取、补接缺失服务并发布前后端。工程声明MySQL自动部署后，会自动建库、建账号和执行迁移；首次在“面板设置”授权已有MySQL即可。目录、数据库或迁移冲突会显示在发布记录，已停止的一端保持停止。</p>
      <p v-if="error" class="error" role="alert">{{ error }}</p><footer class="modal-footer"><button type="button" :disabled="busy" @click="emit('close')">取消</button><button class="primary" :disabled="busy || fetching || !ready">{{ busy ? '保存中…' : deployAfterSave ? '保存并发布' : '保存项目' }}</button></footer>
    </form>
  </section></div>
</template>
<style scoped>
.check-option{display:flex;align-items:center;gap:9px;font-size:13px}.check-option input{width:auto;margin:0}
</style>
