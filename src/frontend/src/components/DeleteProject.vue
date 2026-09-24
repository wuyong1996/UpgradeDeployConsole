<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import { api } from '../api';
import type { DeletionPreview, Project } from '../types';
const props = defineProps<{ project: Project }>();
const emit = defineEmits<{ close: []; submitted: [] }>();
const preview = ref<DeletionPreview>(), confirmation = ref(''), acknowledged = ref(false), busy = ref(false), error = ref('');
const backupBeforeDelete = ref(true), confirmWithoutBackup = ref(false);
const ownershipBlocked = computed(() => /autoManaged|人工接入|归属|旧版项目/.test(error.value));
watch(backupBeforeDelete, () => { confirmWithoutBackup.value = false; acknowledged.value = false; });
let alive = true;
async function load() {
  busy.value = true; error.value = ''; preview.value = undefined;
  try { const result = await api.deletion(props.project.id); if (alive) { preview.value = result; backupBeforeDelete.value = result.backupBeforeDelete !== false; confirmWithoutBackup.value = false; acknowledged.value = false; } }
  catch (e) { if (alive) error.value = e instanceof Error ? e.message : '无法核对删除范围'; }
  finally { if (alive) busy.value = false; }
}
async function submit() {
  if (busy.value || !preview.value || confirmation.value !== props.project.slug || !acknowledged.value || (!backupBeforeDelete.value && !confirmWithoutBackup.value)) return;
  busy.value = true; error.value = '';
  try { await api.deleteProject(props.project, confirmation.value, preview.value.fingerprint, backupBeforeDelete.value, confirmWithoutBackup.value); if (alive) emit('submitted'); }
  catch (e) { if (alive) error.value = e instanceof Error ? e.message : '删除请求失败，请刷新核对结果'; }
  finally { if (alive) busy.value = false; }
}
onMounted(load);
onUnmounted(() => { alive = false; });
</script>
<template>
  <div class="overlay"><section class="modal" role="dialog" aria-modal="true" aria-labelledby="delete-project-title">
    <header class="section-heading"><div><h2 id="delete-project-title">删除项目</h2><p>{{ project.name }} · {{ project.slug }}</p></div><button :disabled="busy" aria-label="关闭删除确认" @click="emit('close')">×</button></header>
    <p class="callout">将删除下列项目资源，包括数据库数据和拉取的 Git 源码。共享 MySQL、Nginx、面板及其他项目保留。默认删除前备份，也可明确选择不备份删除。</p>
    <p v-if="busy && !preview" role="status">正在核对停止状态、资源归属与删除范围…</p>
    <form v-if="preview" @submit.prevent="submit">
      <p v-if="preview.resume" class="hint">上次清理尚未完成，本次按已确认范围继续；已完成的删除不会重复执行。</p>
      <dl class="deletion-resources"><template v-for="(item, index) in preview.resources" :key="index"><dt>{{ item.kind }}</dt><dd class="mono">{{ item.name }}</dd></template><template v-if="backupBeforeDelete && preview.backupDirectory"><dt>恢复备份目录</dt><dd class="mono">{{ preview.backupDirectory }}</dd></template></dl>
      <label class="check-option"><input v-model="backupBeforeDelete" type="checkbox" :disabled="busy || preview.backupModeLocked">删除前备份（推荐）</label>
      <p v-if="preview.backupModeLocked" class="hint">已进入清理阶段，重试沿用原备份选项。</p>
      <p v-if="backupBeforeDelete" class="hint">先备份项目文件和数据库，备份失败则停止删除。</p>
      <template v-else><p class="error" role="alert">不备份删除：本次不会生成文件或数据库恢复备份。独立保存的删除恢复备份保留；项目目录内的历史迁移备份随项目清理。</p><label class="check-option"><input v-model="confirmWithoutBackup" type="checkbox" :disabled="busy">我确认不生成备份，删除后无法通过本次操作恢复数据</label></template>
      <p class="hint">发布记录和审计保留。进入删除后不能启动或发布；若清理中断，请查看记录后重试。</p>
      <label>输入项目标识 {{ project.slug }} 确认<input v-model="confirmation" required autocomplete="off" :disabled="busy"></label>
      <label class="check-option"><input v-model="acknowledged" type="checkbox" :disabled="busy">我确认删除此项目及其数据库数据</label>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <footer class="modal-footer"><button type="button" :disabled="busy" @click="emit('close')">取消</button><button class="danger" :disabled="busy || confirmation !== project.slug || !acknowledged || (!backupBeforeDelete && !confirmWithoutBackup)">{{ busy ? '正在提交…' : !backupBeforeDelete ? '不备份删除项目' : preview.resume ? '确认继续清理（保留备份）' : '备份并删除项目' }}</button></footer>
    </form>
    <template v-else-if="error">
      <p class="error" role="alert">{{ error }}</p>
      <section class="deletion-help" aria-labelledby="delete-help-title">
        <h3 id="delete-help-title">删除前需要做什么</h3>
        <template v-if="ownershipBlocked">
          <p>当前未通过资源归属检查。旧版自动创建的项目可能缺少管理标记，请先安装支持旧版识别的完整升级包，再点击“重新核对”。</p>
          <p>若仍被拒绝，请管理员核对以下接入文件中的服务名、构建账号和目录，并核对实际服务与数据库是否仅属于本项目：</p>
          <p class="mono">/etc/deploy-console/targets/{{ project.slug }}.json</p>
          <p>不要直接修改 autoManaged 标记。人工接入或共享资源需要先明确清理范围，不能通过本按钮强制删除。</p>
        </template>
        <ol>
          <li>总关停已完成，项目没有运行中的服务、构建或发布任务。</li>
          <li>前后端、Git 目录和数据库属于本项目，未与其他项目共用。</li>
          <li>项目数据库账号已暂停访问，没有活动连接；MySQL 管理账号能检查全部连接。</li>
          <li>选择备份时，需要足够空间且备份校验通过；选择不备份时，需明确确认本次无法通过备份恢复。</li>
        </ol>
        <p class="hint">先处理上方拒绝原因，再重新核对。核对通过后才会显示删除清单和输入项目标识的确认框。</p>
      </section>
      <footer class="modal-footer"><button :disabled="busy" @click="emit('close')">关闭</button><button :disabled="busy" @click="load">重新核对</button></footer>
    </template>
  </section></div>
</template>
<style scoped>
.deletion-resources dd{overflow-wrap:anywhere}.check-option{display:flex;align-items:center;gap:9px;margin-top:16px}.check-option input{width:auto;margin:0}
.deletion-help{font-size:13px;line-height:1.7}.deletion-help .mono{overflow-wrap:anywhere}.deletion-help li{margin-bottom:6px}
</style>
