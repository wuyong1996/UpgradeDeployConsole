<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import type { Plan, ServiceStatus, Side, TargetService } from '../types';
import { date, periods, sideNames, stateNames } from '../format';
const props = defineProps<{ side: Side; plan: Plan; status?: ServiceStatus | null; target?: TargetService | null; path: string; busy: boolean; stale: boolean }>();
const emit = defineEmits<{ action: [action: string, side: Side, port?: number]; plan: [side: Side, enabled: boolean, period: number] }>();
const port = ref(props.status?.port || props.target?.port || 8080);
watch(() => props.status?.port, value => { if (value) port.value = value; });
const editPort = ref(false);
const next = computed(() => props.plan.stopped ? '服务已停止，自动发布暂停' : props.plan.periodSeconds === 0 ? '永不自动检查，可手动发布' : !props.plan.autoDeploy ? '自动发布已暂停' : date(props.plan.nextRunAt));
</script>
<template>
  <article class="service-card">
    <header class="section-heading"><h3><span class="service-icon">{{ side === 'front' ? '◫' : '⌘' }}</span>{{ sideNames[side] }}服务</h3><span class="badge" :class="{ green: !stale && status?.state === 'running' && status?.health === 'healthy', red: !stale && status?.health === 'unhealthy' }">{{ stale ? '待刷新' : status ? stateNames[status.state] || status.state : target ? '未知' : '未接入' }}</span></header>
    <div class="metric"><strong>{{ stale ? '—' : status?.port || target?.port || '—' }}</strong><span>{{ side === 'front' ? '访问端口' : '本机接口端口' }}</span><button v-if="target" class="link-button" :disabled="busy" @click="editPort = !editPort">修改</button></div>
    <form v-if="editPort" class="port-editor" @submit.prevent="emit('action', 'port', side, Number(port)); editPort = false"><input v-model.number="port" type="number" min="1" max="65535" required :aria-label="sideNames[side] + '端口'"><button :disabled="busy">应用端口</button></form>
    <dl><template v-if="side === 'front' && status?.accessUrl"><dt>访问地址</dt><dd><a :href="status.accessUrl" target="_blank" rel="noopener noreferrer">{{ status.accessUrl }}</a></dd></template><dt>服务器本地路径</dt><dd class="mono">{{ status?.path || path }}</dd><template v-if="target?.unit"><dt>系统服务名称</dt><dd class="mono">{{ target.unit }}</dd><dt>开机启动</dt><dd>{{ stale || status?.bootEnabled == null ? '待刷新' : status.bootEnabled ? '已开启' : '已关闭' }}</dd></template><dt>运行提交</dt><dd class="mono">{{ status?.commit?.slice(0, 12) || '—' }}</dd><dt>服务类型 / 健康</dt><dd>{{ target?.kind || '未绑定' }} · {{ stale ? '待刷新' : status?.health === 'healthy' ? '健康检查通过' : status?.health === 'unhealthy' ? '健康检查异常' : '未检测' }}</dd></dl>
    <div class="button-row"><button :disabled="busy || !target" @click="emit('action', 'start', side)">启动</button><button :disabled="busy || !target || plan.stopped" @click="emit('action', 'restart', side)">重启</button><button class="danger-light" :disabled="busy || !target" @click="emit('action', 'stop', side)">停止</button><button :disabled="busy || !target || plan.stopped" @click="emit('action', 'deploy', side)">立即发布</button></div>
    <div class="plan"><div class="section-heading"><strong>自动发布</strong><button class="toggle" :class="{ on: plan.autoDeploy && plan.periodSeconds > 0 }" role="switch" :aria-checked="plan.autoDeploy && plan.periodSeconds > 0" :aria-label="sideNames[side] + '自动发布'" :disabled="busy || !target || plan.stopped || plan.periodSeconds === 0" @click="emit('plan', side, !plan.autoDeploy, plan.periodSeconds)"><span /></button></div>
    <label class="inline-label">检查周期<select :value="plan.periodSeconds" :disabled="busy" @change="emit('plan', side, plan.autoDeploy, Number(($event.target as HTMLSelectElement).value))"><option v-for="period in periods" :key="period.value" :value="period.value">{{ period.label }}</option></select></label>
    <span class="hint">下次自动更新 · Asia/Shanghai</span><p class="next-time">{{ next }}</p><span class="hint">最近检查：{{ date(plan.lastCheckAt) }}</span><p v-if="plan.lastResult" class="hint">{{ plan.lastResult }}</p></div>
  </article>
</template>
