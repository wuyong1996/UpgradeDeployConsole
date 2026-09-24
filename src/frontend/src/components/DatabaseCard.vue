<script setup lang="ts">
import type { ServiceStatus, Target } from '../types';
import { stateNames } from '../format';
defineProps<{ target?: Target; status?: ServiceStatus | null; stale: boolean; busy: boolean }>();
const emit = defineEmits<{ start: [] }>();
</script>
<template>
  <article class="service-card database-card">
    <header class="section-heading"><h3><span class="service-icon">▤</span>数据库服务</h3><span class="badge">{{ stale ? '待刷新' : target?.databaseKind === 'none' ? '未配置' : status ? stateNames[status.state] : '未检测' }}</span></header>
    <div class="metric"><strong>{{ status?.port || '—' }}</strong><span>连接端口</span></div>
    <dl>
      <dt>{{ target?.databaseKind === 'mysql' ? '数据库名称' : '服务身份' }}</dt><dd class="mono">{{ status?.databaseName || target?.databaseIdentity || '未绑定' }}</dd>
      <dt>连接地址</dt><dd class="mono">{{ status?.host || '—' }}</dd>
      <template v-if="target?.databaseKind === 'mysql'"><dt>项目数据库账号</dt><dd class="mono">{{ status?.username || '待刷新' }}</dd><dt>迁移状态</dt><dd>{{ stale ? '待刷新' : status?.migrationState === 'pending' ? '待初始化' : status?.migrationState === 'ready' ? '已就绪' : status?.migrationState ? '需查看发布记录' : '未检测' }} · 已应用 {{ status?.appliedMigrations ?? '—' }} 项</dd></template>
      <dt>管理方式</dt><dd>{{ target?.databaseKind === 'mysql' ? '共享 MySQL · 独立项目库与账号' : target?.databaseManaged ? '受管服务 · 纳入总关停' : '仅检测连接' }}</dd>
      <dt>连接健康</dt><dd>{{ stale ? '待刷新' : status?.health === 'healthy' ? '连接正常' : '未连接或未检测' }}</dd>
    </dl>
    <button :disabled="busy || !target?.databaseManaged" @click="emit('start')">{{ target?.databaseKind === 'mysql' ? '恢复数据库访问' : '启动数据库' }}</button>
    <p class="hint">{{ target?.databaseKind === 'mysql' ? '总关停暂停本项目账号，共享 MySQL 继续运行。删除项目会清理独立项目库及账号。' : '总关停后，点击“总开始”按数据库、后端、前端顺序恢复。' }} 启动服务不会自动开启发布计划。</p>
  </article>
</template>
