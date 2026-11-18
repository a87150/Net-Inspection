const NODE_KIND_ORDER = { backbone: 0, endpoint: 1, external: 2 };

function text(value) {
  return String(value ?? '').trim();
}

function compareText(left, right) {
  return text(left).localeCompare(text(right), 'zh-CN');
}

function slug(value, fallback = 'unknown') {
  const normalized = text(value).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  return normalized || fallback;
}

function externalNodeId(edge) {
  const identity = edge.remote_chassis_id
    || edge.remote_system_name
    || (edge.remote_management_addresses || [])[0]
    || edge.id;
  const port = edge.remote_port_id || edge.remote_port_description || 'port';
  return `external:${slug(identity)}:${slug(port)}`;
}

function protocolLabel(protocols) {
  const hasLldp = protocols.some((value) => value.includes('lldp'));
  const hasCdp = protocols.some((value) => value.includes('cdp'));
  if (hasLldp && hasCdp) return 'LLDP/CDP';
  if (hasCdp) return 'CDP';
  if (hasLldp) return 'LLDP';
  return '物理发现';
}

function statusLabel(status) {
  return status === 'current' ? '当前' : status === 'stale' ? '已过期' : '未知';
}

function deviceNodeId(deviceId, nodeIds) {
  const candidate = `networks:${text(deviceId)}`;
  return nodeIds.has(candidate) ? candidate : null;
}

function mergePhysicalEdge(existing, incoming) {
  const protocols = [...new Set([...existing.protocols, ...incoming.protocols])].sort(compareText);
  const isReciprocal = existing.observationCount + incoming.observationCount > 1;
  const status = existing.status === 'current' || incoming.status === 'current' ? 'current' : incoming.status;
  return {
    ...existing,
    protocols,
    protocolLabel: protocolLabel(protocols),
    evidenceDirection: isReciprocal || existing.evidenceDirection === 'bidirectional'
      || incoming.evidenceDirection === 'bidirectional' ? 'bidirectional' : incoming.evidenceDirection,
    status,
    statusLabel: statusLabel(status),
    confidence: Math.max(existing.confidence, incoming.confidence),
    lastSeenAt: [existing.lastSeenAt, incoming.lastSeenAt].filter(Boolean).sort().at(-1) || null,
    observationCount: existing.observationCount + incoming.observationCount,
  };
}

function nodeOrder(left, right) {
  return (NODE_KIND_ORDER[left.kind] ?? 9) - (NODE_KIND_ORDER[right.kind] ?? 9)
    || Number(left.tier ?? 99) - Number(right.tier ?? 99)
    || compareText(left.label, right.label)
    || compareText(left.id, right.id);
}

export function normalizeTopology(snapshot = {}) {
  const sourceNodes = Array.isArray(snapshot.nodes) ? snapshot.nodes : [];
  const baseNodes = sourceNodes
    .filter((node) => node && text(node.id))
    .map((node) => ({
      ...node,
      id: text(node.id),
      kind: text(node.kind) || 'endpoint',
      role: text(node.role) || 'unknown',
      tier: node.tier === null || node.tier === undefined ? null : Number(node.tier),
      parentId: text(node.parent_id) || null,
      attachmentSource: text(node.attachment_source),
      childCount: Number(node.child_count || 0),
    }))
    .sort(nodeOrder);
  const nodeIds = new Set(baseNodes.map((node) => node.id));

  const attachmentEdges = (Array.isArray(snapshot.attachment_edges) ? snapshot.attachment_edges : [])
    .filter((edge) => edge && nodeIds.has(text(edge.source)) && nodeIds.has(text(edge.target)))
    .map((edge) => ({
      id: text(edge.id),
      kind: 'endpoint_attachment',
      source: text(edge.source),
      target: text(edge.target),
      attachmentSource: text(edge.attachment_source),
      protocolLabel: text(edge.attachment_source) === 'physical_discovered' ? '物理发现' : '网段推断',
      status: 'current',
      statusLabel: text(edge.attachment_source) === 'physical_discovered' ? '已发现' : '推断关系',
    }))
    .sort((left, right) => compareText(left.id, right.id));

  const interfaces = new Map(
    (Array.isArray(snapshot.interfaces) ? snapshot.interfaces : [])
      .filter((row) => row && text(row.id))
      .map((row) => [text(row.id), row]),
  );
  const externalNodes = new Map();
  const physicalByEndpoints = new Map();
  const sourceEdges = (Array.isArray(snapshot.physical_edges) ? snapshot.physical_edges : [])
    .filter((edge) => edge && edge.kind === 'physical_discovered')
    .sort((left, right) => compareText(left.id, right.id));

  for (const edge of sourceEdges) {
    const localNode = deviceNodeId(edge.local_device_id, nodeIds);
    if (!localNode) continue;
    let remoteNode = deviceNodeId(edge.remote_device_id, nodeIds);
    if (!remoteNode) {
      remoteNode = externalNodeId(edge);
      if (!externalNodes.has(remoteNode)) {
        externalNodes.set(remoteNode, {
          id: remoteNode,
          kind: 'external',
          role: 'unresolved_neighbor',
          tier: 4,
          label: text(edge.remote_system_name) || text(edge.remote_chassis_id) || '未解析邻居',
          subtitle: text(edge.remote_port_id) || text(edge.remote_management_addresses?.[0]),
          status: edge.status === 'stale' ? 'stale' : 'unknown',
          url: '', parentId: null, attachmentSource: '', childCount: 0,
        });
      }
    }
    if (localNode === remoteNode) continue;

    const localInterface = interfaces.get(text(edge.local_interface_id));
    const remoteInterface = interfaces.get(text(edge.remote_interface_id));
    const isExternal = remoteNode.startsWith('external:');
    const reverseResolved = !isExternal && compareText(localNode, remoteNode) > 0;
    const source = reverseResolved ? remoteNode : localNode;
    const target = reverseResolved ? localNode : remoteNode;
    const protocols = [...new Set(Array.isArray(edge.protocols) ? edge.protocols.map(text) : [])].sort(compareText);
    const currentStatus = text(edge.status) || 'unknown';
    const normalized = {
      id: `physical:${source}:${target}`,
      kind: 'physical_discovered', source, target,
      localInterfaceLabel: reverseResolved
        ? (text(remoteInterface?.name) || text(edge.remote_port_id))
        : (text(localInterface?.name) || text(localInterface?.stable_key)),
      remoteInterfaceLabel: reverseResolved
        ? (text(localInterface?.name) || text(localInterface?.stable_key))
        : (text(remoteInterface?.name) || text(edge.remote_port_id) || text(edge.remote_port_description)),
      protocols,
      protocolLabel: protocolLabel(protocols),
      evidenceDirection: text(edge.evidence_direction) || 'unknown',
      resolutionStatus: text(edge.resolution_status) || (isExternal ? 'unresolved' : 'resolved'),
      status: currentStatus,
      statusLabel: statusLabel(currentStatus),
      speedBps: edge.speed_bps ?? localInterface?.speed_bps ?? null,
      vlanIds: Array.isArray(edge.vlan_ids) ? [...edge.vlan_ids] : (localInterface?.vlan_ids || []),
      confidence: Number(edge.confidence || 0),
      lastSeenAt: edge.last_seen_at || null,
      observationCount: 1,
    };
    const key = `${source}|${target}`;
    physicalByEndpoints.set(
      key,
      physicalByEndpoints.has(key)
        ? mergePhysicalEdge(physicalByEndpoints.get(key), normalized)
        : normalized,
    );
  }

  const nodes = [...baseNodes, ...externalNodes.values()].sort(nodeOrder);
  const physicalEdges = [...physicalByEndpoints.values()]
    .map(({ observationCount, ...edge }) => edge)
    .sort((left, right) => compareText(left.id, right.id));
  return { nodes, attachmentEdges, physicalEdges };
}

export function reconcileExpandedIds(expandedIds, graph) {
  const valid = new Set(
    graph.nodes.filter((node) => node.kind === 'backbone').map((node) => node.id),
  );
  return new Set([...expandedIds].filter((id) => valid.has(id)));
}

export function visibleTopology(graph, expandedIds = new Set()) {
  const nodes = graph.nodes.filter((node) => node.kind === 'backbone'
    || node.kind === 'external'
    || (node.kind === 'endpoint' && node.parentId && expandedIds.has(node.parentId)));
  const ids = new Set(nodes.map((node) => node.id));
  const attachmentEdges = graph.attachmentEdges.filter(
    (edge) => ids.has(edge.source) && ids.has(edge.target),
  );
  const attachmentPairs = new Set(attachmentEdges.map((edge) => `${edge.source}|${edge.target}`));
  const physicalEdges = graph.physicalEdges.filter((edge) => (
    ids.has(edge.source) && ids.has(edge.target)
    && !attachmentPairs.has(`${edge.source}|${edge.target}`)
    && !attachmentPairs.has(`${edge.target}|${edge.source}`)
  ));
  return { ...graph, nodes, attachmentEdges, physicalEdges };
}

function clamp(value, minimum, maximum) {
  return Math.min(Math.max(value, minimum), maximum);
}

/* 中文字与西文字宽度差一倍，按字符类型分别计宽，卡片不会被英文名撑长。 */
const WIDE_CHAR = /[ᄀ-ᅟ⺀-꓏가-힣豈-﫿︰-﹯＀-｠￠-￦]/;

function textWidth(text) {
  let total = 0;
  for (const char of String(text ?? '')) {
    total += WIDE_CHAR.test(char) ? 13 : 7.4;
  }
  return total;
}

/* 卡片宽度按设备名撑开，名字不再截断；宽度也决定同层间距。 */
export function cardWidthFor(node) {
  const minimum = node.kind === 'backbone' ? 148 : 116;
  return Math.max(minimum, Math.round(64 + textWidth(node.label)));
}

/* 一行内按真实宽度均分剩余空间，卡片之间至少留 40px。 */
function distribute(nodes, minX, maxX, y) {
  if (!nodes.length) return [];
  const widths = nodes.map(cardWidthFor);
  const total = widths.reduce((sum, value) => sum + value, 0);
  const span = maxX - minX;
  const gap = nodes.length > 1 ? Math.max(40, (span - total) / (nodes.length - 1)) : 0;
  const rowWidth = total + (gap * (nodes.length - 1));
  const start = minX + Math.max(0, (span - rowWidth) / 2);
  let cursor = start;
  return nodes.map((node, index) => {
    const cardWidth = widths[index];
    const placed = { ...node, cardWidth, x: cursor + (cardWidth / 2), y };
    cursor += cardWidth + gap;
    return placed;
  });
}

/* 贪心装箱：按真实宽度决定一层放几张，放不下的换行，不会挤成一排。 */
function packRows(nodes, available, gap = 40) {
  const rows = [];
  let row = [];
  let used = 0;
  for (const node of nodes) {
    const width = cardWidthFor(node);
    const extra = row.length ? gap : 0;
    if (row.length && (used + extra + width) > available) {
      rows.push(row);
      row = [];
      used = 0;
    }
    row.push(node);
    used += (row.length > 1 ? gap : 0) + width;
  }
  if (row.length) rows.push(row);
  return rows;
}

/* 终端紧跟自己的上级，向下错位避让，与任何已放置卡片都不重叠。 */
// ponytail: 逐个试位的 O(n²) 避让，n 为可见卡片数（几十张），够用；
// 若将来单页要画上千节点，改成按行建索引再二分。
function layoutEndpointGroups(positionedBackbone, endpoints, box) {
  const { padding, width, top = 0, blockers = [] } = box;
  const groups = positionedBackbone
    .map((parent) => ({
      parent,
      children: endpoints.filter((node) => node.parentId === parent.id),
    }))
    .filter((group) => group.children.length)
    .sort((left, right) => left.parent.x - right.parent.x);
  if (!groups.length) return { nodes: [], bottom: top };

  const rowGap = 74;
  const columnGap = 12;
  const available = width - (padding * 2);
  const occupied = blockers.map((node) => ({
    x: node.x,
    y: node.y,
    cardWidth: node.cardWidth,
    halfHeight: node.kind === 'backbone' ? 40 : 33,
  }));
  const placed = [];
  let bottom = top;

  const settle = (centerX, cardWidth, fromY) => {
    let y = fromY;
    for (let guard = 0; guard < 60; guard += 1) {
      const clash = occupied.some((other) => (
        Math.abs(other.x - centerX) < ((other.cardWidth + cardWidth) / 2) + 8
        && Math.abs(other.y - y) < (other.halfHeight + 33)
      ));
      if (!clash) break;
      y += rowGap;
    }
    occupied.push({ x: centerX, y, cardWidth, halfHeight: 33 });
    return Math.round(y);
  };

  for (const group of groups) {
    const items = group.children.map((node) => ({
      node,
      // 名字再长也不能超过一行可用宽度，否则会被整体 clamp 到同一处而重叠。
      cardWidth: Math.min(cardWidthFor(node), available),
    }));
    const baseY = top;
    let index = 0;
    let offsetY = 0;
    while (index < items.length) {
      const row = [];
      let rowWidth = 0;
      while (index + row.length < items.length) {
        const item = items[index + row.length];
        const extra = row.length ? columnGap : 0;
        if (row.length && (rowWidth + extra + item.cardWidth) > available) break;
        rowWidth += extra + item.cardWidth;
        row.push(item);
      }
      const left = clamp(
        group.parent.x - (rowWidth / 2), padding, Math.max(padding, width - padding - rowWidth),
      );
      let cursor = left;
      let rowBottom = 0;
      for (const item of row) {
        const centerX = Math.round(cursor + (item.cardWidth / 2));
        const y = settle(centerX, item.cardWidth, baseY + offsetY);
        rowBottom = Math.max(rowBottom, y);
        placed.push({ ...item.node, cardWidth: item.cardWidth, x: centerX, y });
        cursor += item.cardWidth + columnGap;
      }
      bottom = Math.max(bottom, rowBottom);
      offsetY = rowBottom - baseY + rowGap;
      index += row.length;
    }
  }
  return { nodes: placed, bottom };
}

export function layoutTopology(graph, viewport = {}) {
  const width = Math.max(320, Number(viewport.width) || 960);
  const height = Math.max(620, Number(viewport.height) || 620);
  const padding = width < 480 ? 48 : 72;
  const backbone = graph.nodes.filter((node) => node.kind === 'backbone');
  const endpoints = graph.nodes.filter((node) => node.kind === 'endpoint');
  const external = graph.nodes.filter((node) => node.kind === 'external');
  const tiers = [...new Set(backbone.map((node) => Number(node.tier ?? 3)))].sort((a, b) => a - b);
  const available = width - (padding * 2);
  const positionedBackbone = [];
  const positionedEndpoints = [];
  let tierY = padding + 46;
  for (const tier of tiers) {
    const members = backbone.filter((node) => Number(node.tier ?? 3) === tier);
    for (const row of packRows(members, available)) {
      positionedBackbone.push(...distribute(row, padding, width - padding, tierY));
      tierY += 118;
    }
    // 该层展开的终端紧跟本层卡片，下一层从它们下方继续，层间不会被压穿。
    const tierParents = positionedBackbone.filter(
      (node) => Number(node.tier ?? 3) === tier,
    );
    const children = endpoints.filter(
      (node) => tierParents.some((parent) => parent.id === node.parentId),
    );
    if (children.length) {
      const laid = layoutEndpointGroups(tierParents, children, {
        padding,
        width,
        top: Math.max(...tierParents.map((node) => node.y)) + 74,
        blockers: [...positionedBackbone, ...positionedEndpoints],
      });
      positionedEndpoints.push(...laid.nodes);
      tierY = laid.bottom + 74;
    }
    tierY += 35;
  }
  // 展开的终端可能超出一屏高度：画布跟着长高，别把它们压回去重叠。
  const contentBottom = Math.max(
    height - padding - 90,
    ...positionedBackbone.map((node) => node.y),
    ...positionedEndpoints.map((node) => node.y),
  );
  const positionedExternal = distribute(
    external, padding, width - padding, contentBottom + 110,
  );
  const positioned = [...positionedBackbone, ...positionedEndpoints, ...positionedExternal]
    .map((node) => ({
      ...node,
      x: Math.round(clamp(node.x, padding, width - padding)),
      y: Math.round(Math.max(node.y, padding)),
    }));
  const contentMaxY = Math.max(
    height - padding,
    ...positioned.map((node) => node.y + 44),
  );

  return {
    nodes: positioned,
    attachmentEdges: graph.attachmentEdges.map((edge) => ({ ...edge })),
    physicalEdges: graph.physicalEdges.map((edge) => ({ ...edge })),
    legend: [
      { kind: 'physical', label: 'LLDP/CDP 物理链路' },
      { kind: 'inferred', label: '网段推断终端' },
      { kind: 'external', label: '未解析邻居' },
    ],
    bounds: { minX: padding, minY: padding, maxX: width - padding, maxY: contentMaxY },
  };
}
