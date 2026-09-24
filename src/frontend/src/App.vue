<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { api, ApiError } from './api';
import { actionNames, date, nextSlug, sideNames, stateNames } from './format';
import { needsStartAll } from './project-controls';
import type { Audit, HostResult, Job, Project, ProjectInput, Settings, Side, Target } from './types';
import ProjectForm from './components/ProjectForm.vue';
import ServiceCard from './components/ServiceCard.vue';
import MySqlSettings from './components/MySqlSettings.vue';
import HttpsSettings from './components/HttpsSettings.vue';
import DatabaseCard from './components/DatabaseCard.vue';
import DeleteProject from './components/DeleteProject.vue';

const logged = ref(false), booting = ref(true), password = ref(''), showPassword = ref(false), busy = ref(false), refreshing = ref(false);
const error = ref(''), notice = ref(''), formError = ref('');
const projects = ref<Project[]>([]), targets = ref<Target[]>([]), settings = ref<Settings>();
const statuses = ref<Record<string, HostResult>>({}), stale = ref<Record<string, boolean>>({}), updatedAt = ref<string | null>(null);
const selectedId = ref<string>(), page = ref<'projects' | 'history' | 'settings'>('projects'), tab = ref('services'), search = ref('');
const deleteOpen = ref(false);
const formOpen = ref(false), editing = ref<Project>(), jobs = ref<Job[]>([]), jobPage = ref(1), jobTotal = ref(0), audit = ref<Audit[]>([]), auditPage = ref(1), auditTotal = ref(0);
let alive = true, timer: number | undefined, refreshNumber = 0;
const selected = computed(() => projects.value.find(x => x.id === selectedId.value));
const binding = computed(() => targets.value.find(x => x.slug === selected.value?.slug));
const selectedStatus = computed(() => selected.value ? statuses.value[selected.value.id] : undefined);
const filtered = computed(() => projects.value.filter(x => (x.name + x.slug + x.repository).toLowerCase().includes(search.value.toLowerCase())));
const activeJobs = computed(() => jobs.value.filter(x => x.state === 'running' || x.state === 'queued'));
const resumeAll = computed(() => needsStartAll(selected.value, binding.value, selectedStatus.value, stale.value[selected.value?.id ?? ''] ?? true)
  && !activeJobs.value.some(x => x.action === 'start-all'));
const sharedDatabase = computed(() => { const identity = binding.value?.databaseIdentity; return identity ? projects.value.filter(p => p.id !== selectedId.value && targets.value.some(t => t.slug === p.slug && t.databaseIdentity === identity && t.databaseKind === binding.value?.databaseKind)) : []; });
function fail(exception: unknown) { if (!alive) return; error.value = exception instanceof Error ? exception.message : '操作失败'; if (exception instanceof ApiError && exception.status === 401) { logged.value = false; password.value = ''; } }
async function work(action: () => Promise<void>) { if (busy.value) return; busy.value = true; error.value = ''; notice.value = ''; try { await action(); } catch (exception) { fail(exception); } finally { if (alive) busy.value = false; } }
async function login() { await work(async () => { await api.login(password.value); if (!alive) return; password.value = ''; logged.value = true; await refresh(); }); }
async function logout() { await work(async () => { await api.logout(); if (alive) { logged.value = false; selectedId.value = undefined; } }); }
async function history() { const id = selectedId.value, number = jobPage.value; const result = await api.jobs(id, number); if (alive && id === selectedId.value && number === jobPage.value) { jobs.value = result.items; jobTotal.value = result.total; } }
async function loadAudit() { const result = await api.audit(auditPage.value); if (alive) { audit.value = result.items; auditTotal.value = result.total; } }
async function refresh() {
  if (!logged.value || refreshing.value) return;
  refreshing.value = true;
  try {
    const result = await Promise.all([api.projects(), api.targets(), api.settings()]);
    if (!alive || !logged.value) return;
    if (selectedId.value && !result[0].some(p => p.id === selectedId.value)) { selectedId.value = undefined; deleteOpen.value = false; page.value = 'history'; jobPage.value = 1; notice.value = '项目已删除，删除方式与结果请查看操作记录'; }
    projects.value = result[0]; targets.value = result[1]; settings.value = result[2];
    await history();
    if (page.value === 'settings') await loadAudit();
    // Limit concurrent host probes; failed requests explicitly invalidate old snapshots.
    for (let i = 0; i < projects.value.length; i += 4) {
      await Promise.all(projects.value.slice(i, i + 4).map(async project => {
        try { const status = await api.status(project.id); if (alive) { statuses.value[project.id] = status; stale.value[project.id] = !status.ok; } }
        catch (exception) { if (alive) stale.value[project.id] = true; fail(exception); }
      }));
    }
    if (alive) updatedAt.value = new Date().toISOString();
  } catch (exception) { for (const project of projects.value) stale.value[project.id] = true; fail(exception); }
  finally { if (alive) refreshing.value = false; }
}
function navigate(destination: 'projects' | 'history' | 'settings') { page.value = destination; selectedId.value = undefined; jobPage.value = 1; void work(async () => { await history(); if (destination === 'settings') await loadAudit(); }); }
function openProject(project: Project, destination = 'services') { selectedId.value = project.id; tab.value = destination; jobPage.value = 1; void work(history); }
function edit(project?: Project) { editing.value = project; formError.value = ''; formOpen.value = true; }
async function save(input: ProjectInput) { if (busy.value) return; busy.value = true; formError.value = ''; try { const saved = await api.save(input, editing.value?.id); if (!alive) return; formOpen.value = false; selectedId.value = saved.id; tab.value = input.deployAfterSave ? 'history' : 'services'; page.value = 'projects'; notice.value = input.deployAfterSave ? '项目已保存，拉取和发布任务已提交，请查看下方进度' : '项目配置已保存'; await refresh(); } catch (exception) { if (alive) formError.value = exception instanceof Error ? exception.message : '保存失败'; if (exception instanceof ApiError && exception.status === 401) fail(exception); } finally { if (alive) busy.value = false; } }
async function action(actionName: string, side = '', port?: number) { const project = selected.value; if (!project) return; await work(async () => { await api.job(project, actionName, side, port); notice.value = `${sideNames[side] || ''}${actionNames[actionName]}请求已提交，可在操作记录查看结果`; await refresh(); }); }
async function plan(side: Side, enabled: boolean, period: number) { const project = selected.value; if (!project) return; await work(async () => { const result = await api.plan(project, side, enabled, period); projects.value = projects.value.map(x => x.id === result.id ? result : x); notice.value = `${sideNames[side]}检查计划已保存`; }); }
async function deletionSubmitted() { deleteOpen.value = false; tab.value = 'history'; notice.value = '删除任务已提交，正在备份并清理项目资源'; await refresh(); }
async function turnHistory(delta: number) { jobPage.value += delta; await work(history); }
async function turnAudit(delta: number) { auditPage.value += delta; await work(loadAudit); }
onMounted(async () => {
  try { await api.session(); if (alive) { logged.value = true; await refresh(); } }
  catch (exception) { if (!(exception instanceof ApiError && exception.status === 401)) fail(exception); }
  finally { if (alive) booting.value = false; }
  if (alive) timer = window.setInterval(() => { if (logged.value && !busy.value && !formOpen.value && !deleteOpen.value) { refreshNumber++; if (refreshNumber % 3 === 0) void refresh(); else void history().catch(fail); } }, 3000);
});
onUnmounted(() => { alive = false; window.clearInterval(timer); });
</script>

<template>
  <main v-if="booting" class="login-shell"><p>正在连接服务器更新管理…</p></main>
  <main v-else-if="!logged" class="login-shell">
    <div class="login-art"><span class="brand-mark">D</span><span class="eyebrow">DEPLOY CONSOLE</span><h1>让每一次更新，<br>有迹可循。</h1><p>集中管理项目、服务与发布计划。</p><div class="orbit orbit-one"/><div class="orbit orbit-two"/></div>
    <section class="login-card"><span class="eyebrow">服务器更新管理</span><h2>登录管理面板</h2><p class="muted">使用服务器配置的访问密码继续。</p><form @submit.prevent="login"><label>访问密码<div class="password-field"><input v-model="password" :type="showPassword ? 'text' : 'password'" required autocomplete="current-password" maxlength="4096" autofocus><button type="button" @click="showPassword = !showPassword">{{ showPassword ? '隐藏' : '显示' }}</button></div></label><p v-if="error" class="error" role="alert">{{ error }}</p><button class="primary login-submit" :disabled="busy">{{ busy ? '登录中…' : '进入管理面板 →' }}</button></form><p class="hint">密码由服务器上的文本文件维护。修改后需重新登录。</p></section>
  </main>
  <div v-else class="shell">
    <aside class="sidebar"><a class="brand" href="#" @click.prevent="navigate('projects')"><span class="brand-mark">D</span><div>服务器更新管理<small>DEPLOY CONSOLE</small></div></a><span class="nav-label">工作空间</span><nav><button :class="{ active: page === 'projects' }" @click="navigate('projects')"><span>◫</span>项目总览<small>{{ projects.length }}</small></button><button :class="{ active: page === 'history' }" @click="navigate('history')"><span>↻</span>发布记录</button><button :class="{ active: page === 'settings' }" @click="navigate('settings')"><span>⚙</span>面板设置</button></nav><div class="sidebar-note"><span class="dot"/>独立管理进程<p>关停项目后<br>管理面板仍可访问</p></div></aside>
    <div class="main-column"><header class="topbar"><span>工作空间 <span class="muted">/ {{ page === 'settings' ? '面板设置' : page === 'history' ? '发布记录' : '项目管理' }}</span></span><div class="button-row"><span class="muted small">Asia/Shanghai</span><span class="avatar">A</span><button class="link-button" :disabled="busy" @click="logout">退出</button></div></header>
    <main class="content">
      <div v-if="error" class="banner error" role="alert">{{ error }}<button @click="error = ''" aria-label="关闭提示">×</button></div><div v-if="notice" class="banner success" role="status">{{ notice }}<button @click="notice = ''" aria-label="关闭提示">×</button></div>
      <p v-if="settings && !settings.linux" class="callout">当前是 Windows 本地预览。配置会真实保存；Linux 服务状态与启停将在服务器接入后可用。</p>
      <template v-if="page === 'projects' && !selected">
        <header class="page-heading"><div><span class="eyebrow">PROJECTS</span><h1>项目总览</h1><p class="muted">查看服务状态，掌握每个项目的更新节奏。</p></div><div class="button-row"><button :disabled="refreshing" @click="refresh">{{ refreshing ? '刷新中…' : '↻ 刷新' }}</button><button class="primary" @click="edit()">＋ 添加项目</button></div></header>
        <div class="stats"><div><span>管理项目</span><strong>{{ projects.length.toString().padStart(2, '0') }}</strong><small>独立配置与部署目录</small></div><div><span>已绑定服务</span><strong>{{ projects.filter(x => targets.some(t => t.slug === x.slug)).length.toString().padStart(2, '0') }}</strong><small>服务器接入文件已配置</small></div><div><span>自动发布计划</span><strong>{{ projects.reduce((n, p) => n + Number(p.front.autoDeploy && p.front.periodSeconds > 0) + Number(p.back.autoDeploy && p.back.periodSeconds > 0), 0).toString().padStart(2, '0') }}</strong><small>前端、后端分别调度</small></div><div><span>本页待完成操作</span><strong>{{ activeJobs.length.toString().padStart(2, '0') }}</strong><small>结果见发布记录</small></div></div>
        <section class="panel"><header class="panel-toolbar"><h2>全部项目 <span class="count">{{ projects.length }}</span></h2><input v-model="search" class="search" placeholder="搜索项目名称、Git 地址" aria-label="搜索项目"></header>
          <div v-if="!projects.length" class="empty"><span class="empty-icon">◫</span><h2>从第一个项目开始</h2><p>添加 Git 地址与部署目录，保存后自动拉取代码并发布。</p><button class="primary" @click="edit()">＋ 添加项目</button><p class="hint">项目标识按名称自动生成 · 目录可手动调整</p></div>
          <div v-else class="project-table"><div class="table-head"><span>项目 / Git 来源</span><span>前端服务</span><span>后端服务</span><span>数据库</span><span>操作</span></div><article v-for="project in filtered" :key="project.id" class="project-row"><div class="project-info"><div class="button-row"><button class="project-name" @click="openProject(project)">{{ project.name }}</button><span class="badge" :class="{ amber: project.environment === 'production' }">{{ project.environment === 'production' ? '正式' : '测试' }}</span></div><span class="mono small">{{ project.slug }} · {{ project.branch }}</span><p class="git-url" :title="project.repository">{{ project.repository }}</p><span v-if="!targets.some(t => t.slug === project.slug)" class="hint">待完成首次发布</span></div>
          <div v-for="side in (['front', 'back'] as const)" :key="side" class="service-summary"><span class="mobile-label">{{ sideNames[side] }}</span><span class="status-line"><span class="dot" :class="{ gray: stale[project.id] || statuses[project.id]?.[side]?.state !== 'running', red: !stale[project.id] && statuses[project.id]?.[side]?.health === 'unhealthy' }"/>{{ stale[project.id] ? '未检测' : stateNames[statuses[project.id]?.[side]?.state || 'unknown'] }} <b>:{{ statuses[project.id]?.[side]?.port || '—' }}</b></span><small>{{ project[side].stopped ? '自动发布已暂停' : project[side].periodSeconds === 0 ? '永不自动检查' : project[side].autoDeploy ? '自动发布开启' : '自动发布关闭' }}</small><small>下次：{{ project[side].autoDeploy && project[side].periodSeconds > 0 ? date(project[side].nextRunAt) : '—' }}</small></div>
          <div class="service-summary"><span class="mobile-label">数据库</span><span>{{ stale[project.id] ? '未检测' : stateNames[statuses[project.id]?.database?.state || 'unknown'] }}</span><small>端口 :{{ statuses[project.id]?.database?.port || '—' }}</small><small>{{ statuses[project.id]?.database?.health === 'healthy' ? '连接正常' : '连接待检测' }}</small></div><div><button class="link-button" @click="openProject(project)">管理项目 →</button></div></article><p v-if="!filtered.length" class="empty">没有匹配项目</p></div>
          <footer class="panel-footer">最近刷新：{{ date(updatedAt) }} · 页面每 9 秒刷新状态，失败后标记为待刷新</footer>
        </section>
      </template>
      <template v-else-if="page === 'projects' && selected">
        <button class="back-button" @click="selectedId = undefined; jobPage = 1; void work(history)">← 返回项目总览</button><header class="page-heading"><div><span class="eyebrow">{{ selected.slug }}</span><h1>{{ selected.name }} <span class="badge">{{ selected.environment === 'production' ? '正式环境' : '测试环境' }}</span></h1><p class="muted">{{ selected.branch }} · {{ selected.repository }}</p></div><div class="button-row"><button :class="resumeAll ? 'primary' : 'danger'" :disabled="busy || selected.deletionRequested || (!binding && !activeJobs.length) || (resumeAll && activeJobs.length > 0)" @click="action(resumeAll ? 'start-all' : 'stop-all')">{{ resumeAll ? '▶ 总开始' : '■ 总关停' }}</button><button class="primary" :disabled="busy || selected.deletionRequested || activeJobs.length > 0" @click="action('publish')">{{ !binding && selected.shutdownRequested ? '恢复并发布' : '发布项目' }}</button><button :disabled="busy || selected.deletionRequested || !binding" @click="action('check')">↻ 检查更新</button><button v-if="selected.shutdownRequested && selected.front.stopped && selected.back.stopped" class="danger-light" :disabled="busy || activeJobs.length > 0" @click="deleteOpen = true">{{ selected.deletionRequested ? '重试删除' : '删除项目' }}</button><button :disabled="refreshing" @click="refresh">刷新</button></div></header>
        <p v-if="!binding" class="callout">首次接入尚未完成。保存并发布后会自动拉取代码和准备服务；请查看发布记录，失败后可修复原因并点击“发布项目”重试。</p><p v-else-if="selectedStatus && !selectedStatus.ok" class="callout">{{ selectedStatus.message }}</p>
        <p v-if="selected.deletionRequested" class="callout">项目已进入删除流程，启动、编辑与发布已停用。清理失败时请查看记录，并点击“重试删除”核对后继续。</p><p v-if="resumeAll" class="callout">项目已停止。点击“总开始”依次恢复数据库、后端和前端；数据库待初始化时会先完成初始化。恢复后自动发布计划仍保持暂停，可按需重新开启。</p>
        <p v-if="activeJobs.length" class="callout" role="status">正在执行：{{ activeJobs[0]?.step }} <button class="link-button" @click="tab = 'history'">查看发布进度</button></p><p v-if="binding?.databaseKind === 'external' || (binding?.databaseKind !== 'none' && !binding?.databaseManaged && binding)" class="callout">数据库仅供连接检测，尚未接管启停。总关停会被阻止，不会先停一部分服务。</p><p v-if="sharedDatabase.length" class="callout">共享数据库：总关停也会影响 {{ sharedDatabase.map(p => p.name).join('、') }} 的数据库连接。</p>
        <div class="tabs"><button v-for="item in [{ key: 'services', title: '服务与更新计划' }, { key: 'git', title: 'Git 与目录' }, { key: 'history', title: '发布记录' }]" :key="item.key" :class="{ active: tab === item.key }" @click="tab = item.key">{{ item.title }}</button></div>
        <template v-if="tab === 'services'"><p class="callout">Git 源码与构建缓存目录：<span class="mono">{{ binding?.repositoryPath || selected.repositoryPath }}</span></p><div class="service-grid"><ServiceCard v-for="side in (['front', 'back'] as const)" :key="side" :side="side" :plan="selected[side]" :status="selectedStatus?.[side]" :target="binding?.[side]" :path="side === 'front' ? selected.frontPath : selected.backPath" :busy="busy || selected.deletionRequested" :stale="stale[selected.id] ?? true" @action="action" @plan="plan"/><DatabaseCard :target="binding" :status="selectedStatus?.database" :stale="stale[selected.id] ?? true" :busy="busy || selected.deletionRequested" @start="action('start', 'database')"/></div><p class="footnote">停止某一端会同步暂停该端自动发布，另一端保持原计划。Nginx 站点停止后返回 503，不关闭其他站点。</p></template>
        <section v-else-if="tab === 'git'" class="panel configuration"><header class="section-heading"><div><h2>Git 来源与部署目录</h2><p class="muted">保存并发布会在后台拉取所选分支；仅保存配置时保持旧发布计划暂停。</p></div><button class="primary" :disabled="busy || selected.deletionRequested" @click="edit(selected)">编辑配置</button></header><dl><dt>Git 地址</dt><dd class="mono">{{ selected.repository }}</dd><dt>跟踪分支</dt><dd>{{ selected.branch }}</dd><dt>源码本地路径</dt><dd class="mono">{{ selected.repositoryPath }}</dd><dt>前端部署路径</dt><dd class="mono">{{ selected.frontPath }}</dd><dt>后端部署路径</dt><dd class="mono">{{ selected.backPath }}</dd><dt>允许的 Git 主机</dt><dd>{{ binding?.allowedGitHosts.join('、') || '由服务器接入文件设置' }}</dd></dl></section>
      </template>
      <template v-if="page === 'history' || (page === 'projects' && selected && tab === 'history')">
        <header v-if="page === 'history'" class="page-heading"><div><span class="eyebrow">DEPLOYMENT HISTORY</span><h1>发布记录</h1><p class="muted">查看检查、发布与服务操作的结果。</p></div><button :disabled="busy" @click="work(history)">刷新记录</button></header><section class="panel history-panel"><p v-if="!jobs.length" class="empty">暂无操作记录</p><details v-for="job in jobs" :key="job.id" class="job" :open="job.state === 'failed' && job.id === jobs[0]?.id"><summary><span class="badge" :class="{ green: job.state === 'succeeded', red: job.state === 'failed', amber: job.state === 'running' }">{{ stateNames[job.state] }}</span><strong>{{ sideNames[job.side] }}{{ actionNames[job.action] || job.action }}</strong><span class="muted">{{ projects.find(p => p.id === job.projectId)?.name || job.projectName || '已删除项目' }} · {{ job.trigger === 'schedule' ? '周期自动发布' : job.trigger === 'save' ? '项目发布流程' : '手动' }}</span><time>{{ date(job.createdAt) }}</time></summary><p>{{ job.step }}</p><p v-if="job.action === 'delete'" class="hint">删除选项：{{ job.backupBeforeDelete === false ? '不备份删除' : '删除前备份' }}</p><p v-if="job.result?.backupDirectory" class="mono">恢复备份：{{ job.result.backupDirectory }}</p><p v-if="job.result?.commit" class="mono">提交：{{ job.result.commit }}</p><ol class="events"><li v-for="(event, index) in job.events" :key="index"><time>{{ date(event.at) }}</time>{{ event.message }}</li></ol><div v-if="job.result?.branches.length" class="branches"><span v-for="branch in job.result.branches" :key="branch.name" class="badge">{{ branch.name }} · {{ branch.commit.slice(0, 8) }}</span></div><button v-if="selected && job.state === 'succeeded' && (job.action === 'deploy' || job.action === 'rollback')" :disabled="busy || selected.deletionRequested" @click="action('rollback', job.side)">将{{ sideNames[job.side] }}回退至上一应用版本</button><p class="hint">{{ job.action === 'delete' ? (job.backupBeforeDelete === false ? '本次选择不备份删除，不生成本次恢复备份。' : '删除后的恢复需由管理员核验备份并执行，不能使用应用版本回退。') : '应用回退不改变数据库。兼容性由项目接入配置及发布流程保障。' }}</p></details><footer class="pagination"><span>共 {{ jobTotal }} 条</span><button :disabled="busy || jobPage <= 1" @click="turnHistory(-1)">上一页</button><span>{{ jobPage }}</span><button :disabled="busy || jobPage * 20 >= jobTotal" @click="turnHistory(1)">下一页</button></footer></section>
      </template>
      <template v-if="page === 'settings'"><header class="page-heading"><div><span class="eyebrow">CONSOLE SETTINGS</span><h1>面板设置</h1><p class="muted">访问配置与操作审计。</p></div></header><section class="panel configuration"><h2>访问密码</h2><p>初始密码为 admin。修改下面的服务器文本文件即可更换密码，原有会话会失效。</p><p class="code-block">{{ settings?.passwordFile }}</p><p class="hint">UTF-8 单行文本。文件缺失或为空时禁止登录；安装升级不会重置已有密码。</p><div class="divider"/><h3>运行环境</h3><p>{{ settings?.linux ? 'Linux 服务控制已启用' : 'Windows 本地配置预览' }} · 时区 {{ settings?.timezone }} · {{ settings?.localMode ? '仅回环访问' : 'HTTPS 访问' }}</p></section><HttpsSettings v-if="settings" :linux="settings.linux" @saved="refresh"/><MySqlSettings v-if="settings" :linux="settings.linux"/><section class="panel history-panel"><header class="panel-toolbar"><h2>操作审计</h2></header><div v-for="(entry, index) in audit" :key="index" class="audit-row"><time>{{ date(entry.at) }}</time><strong>{{ entry.action }}</strong><span>{{ entry.detail || '—' }}</span></div><footer class="pagination"><span>共 {{ auditTotal }} 条</span><button :disabled="busy || auditPage <= 1" @click="turnAudit(-1)">上一页</button><span>{{ auditPage }}</span><button :disabled="busy || auditPage * 20 >= auditTotal" @click="turnAudit(1)">下一页</button></footer></section></template>
    </main></div>
    <DeleteProject v-if="deleteOpen && selected" :project="selected" @close="deleteOpen = false" @submitted="deletionSubmitted"/>
    <ProjectForm v-if="formOpen" :project="editing" :slug="nextSlug(projects.map(x => x.slug))" :used-slugs="projects.map(x => x.slug)" :targets="targets" :busy="busy" :error="formError" @close="formOpen = false" @save="save"/>
  </div>
</template>
