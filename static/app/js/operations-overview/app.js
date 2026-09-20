import { createApp } from '../../../vendor/vue/vue.esm-browser.prod.js';
import { createRefreshController } from './state.js';
import { normalizeTopology, layoutTopology } from './topology.js';
import { createAmbientEffects } from './effects.js';

const mountElement = document.querySelector('#operations-overview-app');
const bootstrapElement = document.querySelector('#operations-overview-bootstrap');

if (mountElement && bootstrapElement) {
  const initialSnapshot = JSON.parse(bootstrapElement.textContent);
  const endpoint = mountElement.dataset.snapshotUrl;

  createApp({
    delimiters: ['[[', ']]'],
    data() {
      return {
        snapshot: initialSnapshot,
        refreshing: false,
        stale: false,
        error: '',
        manualPaused: false,
        hidden: document.hidden,
        view: 'situation',
        filter: 'all',
        viewport: { scale: 1, x: 0, y: 0 },
        topologyWidth: 1100,
        topologyHeight: 620,
        selectedEdgeId: '',
        liveStatus: '综合展示已就绪',
        controller: null,
        unsubscribe: null,
        ambientEffects: null,
        panStart: null,
      };
    },
    computed: {
      generatedLabel() {
        return this.formatTime(this.snapshot.generated_at);
      },
      summaryCards() {
        const labels = {
          people: ['人员', '/assets/people/'],
          computers: ['PC', '/assets/computers/'],
          networks: ['网络设备', '/assets/networks/'],
          servers: ['服务器', '/assets/servers/'],
          monitors: ['安防设备', '/assets/monitors/'],
          domain: ['域控对象', '/domain/accounts/'],
          tasks: ['巡检任务', '/tasks/'],
        };
        return Object.entries(labels).map(([key, [label, url]]) => {
          const value = this.snapshot.summary?.[key] || {};
          return {
            key, label, url,
            total: value.total ?? 0,
            normal: value.normal ?? Math.max(0, (value.total ?? 0) - (value.failed ?? 0)),
            abnormal: value.abnormal ?? value.failed ?? 0,
            unchecked: value.unchecked ?? value.running ?? 0,
          };
        });
      },
      tasks() {
        return this.snapshot.tasks?.items || [];
      },
      alerts() {
        return this.snapshot.alerts?.items || [];
      },
      topology() {
        const graph = normalizeTopology(this.snapshot.topology || {});
        return layoutTopology(graph, { width: this.topologyWidth, height: this.topologyHeight });
      },
      topologyNodes() {
        return new Map(this.topology.nodes.map((node) => [node.id, node]));
      },
      selectedEdge() {
        return this.topology.physicalEdges.find((edge) => edge.id === this.selectedEdgeId) || null;
      },
    },
    methods: {
      setView(view) {
        this.view = view;
        this.controller?.updateUiState({ view });
        this.liveStatus = view === 'topology' ? '已切换到网络拓扑' : '已切换到运维态势';
      },
      togglePause() {
        this.controller?.setManualPaused(!this.manualPaused);
        this.liveStatus = this.manualPaused ? '自动刷新已暂停' : '自动刷新已恢复';
      },
      async refreshNow() {
        await this.controller?.refresh();
        this.liveStatus = this.stale ? this.error : '数据刷新成功';
      },
      syncControllerState(next) {
        const previousGeneratedAt = this.snapshot?.generated_at;
        this.snapshot = next.lastSnapshot;
        this.refreshing = next.refreshing;
        this.stale = next.stale;
        this.error = next.error;
        this.manualPaused = next.manualPaused;
        this.hidden = next.hidden;
        this.view = next.view;
        this.filter = next.filter;
        this.viewport = { ...next.viewport };
        if (previousGeneratedAt !== this.snapshot?.generated_at && !next.refreshing) {
          this.liveStatus = '综合展示数据已更新';
        }
      },
      updateDimensions() {
        const shell = mountElement.querySelector('.topology-canvas-shell');
        const width = shell?.clientWidth || window.innerWidth;
        this.topologyWidth = Math.max(320, Math.min(1400, width));
        this.topologyHeight = window.innerWidth < 768 ? 520 : 620;
      },
      zoomBy(delta) {
        const scale = Math.min(2.2, Math.max(0.6, this.viewport.scale + delta));
        this.viewport = { ...this.viewport, scale: Number(scale.toFixed(2)) };
        this.controller?.updateUiState({ viewport: this.viewport });
      },
      resetViewport() {
        this.viewport = { scale: 1, x: 0, y: 0 };
        this.controller?.updateUiState({ viewport: this.viewport });
      },
      startPan(event) {
        event.currentTarget.setPointerCapture?.(event.pointerId);
        this.panStart = { pointerId: event.pointerId, x: event.clientX, y: event.clientY,
          originX: this.viewport.x, originY: this.viewport.y };
      },
      movePan(event) {
        if (!this.panStart || this.panStart.pointerId !== event.pointerId) return;
        this.viewport = {
          ...this.viewport,
          x: this.panStart.originX + event.clientX - this.panStart.x,
          y: this.panStart.originY + event.clientY - this.panStart.y,
        };
      },
      endPan(event) {
        if (!this.panStart || this.panStart.pointerId !== event.pointerId) return;
        this.panStart = null;
        this.controller?.updateUiState({ viewport: this.viewport });
      },
      edgePath(edge) {
        const source = this.topologyNodes.get(edge.source);
        const target = this.topologyNodes.get(edge.target);
        if (!source || !target) return '';
        const curve = Math.max(30, Math.abs(target.x - source.x) * 0.45);
        return `M ${source.x} ${source.y} C ${source.x + curve} ${source.y}, ${target.x - curve} ${target.y}, ${target.x} ${target.y}`;
      },
      selectEdge(edge) {
        this.selectedEdgeId = edge.id;
        this.liveStatus = `已选择 ${edge.protocolLabel} 物理链路`;
      },
      edgeAriaLabel(edge) {
        const source = this.topologyNodes.get(edge.source)?.label || '未知设备';
        const target = this.topologyNodes.get(edge.target)?.label || '未知邻居';
        return `${source} 到 ${target}，${edge.protocolLabel}，${edge.statusLabel}`;
      },
      truncate(value, length) {
        const stringValue = String(value || '');
        return stringValue.length > length ? `${stringValue.slice(0, length - 1)}…` : stringValue;
      },
      formatTime(value) {
        if (!value) return '暂无时间';
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return '时间未知';
        return new Intl.DateTimeFormat('zh-CN', {
          month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
        }).format(date);
      },
      formatSpeed(value) {
        const speed = Number(value || 0);
        if (!speed) return '速率未知';
        if (speed >= 1e9) return `${(speed / 1e9).toFixed(speed % 1e9 ? 1 : 0)} Gbps`;
        if (speed >= 1e6) return `${(speed / 1e6).toFixed(speed % 1e6 ? 1 : 0)} Mbps`;
        return `${speed} bps`;
      },
      formatConfidence(value) {
        return `${Math.round(Number(value || 0) * 100)}%`;
      },
      directionLabel(value) {
        return value === 'bidirectional' ? '双向发现' : value === 'outbound' ? '本端发现' : '方向未知';
      },
      resolutionLabel(value) {
        return value === 'resolved' ? '已解析设备' : '未解析邻居';
      },
      handleVisibility() {
        this.controller?.handleVisibilityChange();
      },
    },
    mounted() {
      this.controller = createRefreshController({
        initialSnapshot,
        intervalMs: 30000,
        fetchSnapshot: async () => {
          const response = await fetch(endpoint, {
            credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' },
          });
          if (!response.ok) throw new Error(`Snapshot request failed: ${response.status}`);
          return response.json();
        },
        isVisible: () => !document.hidden,
      });
      this.unsubscribe = this.controller.subscribe(this.syncControllerState);
      this.controller.start();
      document.addEventListener('visibilitychange', this.handleVisibility);
      window.addEventListener('resize', this.updateDimensions, { passive: true });
      this.updateDimensions();
      this.ambientEffects = createAmbientEffects(document.querySelector('[data-overview-ambient]'));
    },
    beforeUnmount() {
      this.controller?.stop();
      this.unsubscribe?.();
      this.ambientEffects?.destroy();
      document.removeEventListener('visibilitychange', this.handleVisibility);
      window.removeEventListener('resize', this.updateDimensions);
    },
  }).mount(mountElement);
}
