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
      site: text(node.site),
      siteId: node.site_id === null || node.site_id === undefined ? null : Number(node.site_id),
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
      vpn: Boolean(edge.vpn),
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
  const hierarchyEdges = (Array.isArray(snapshot.hierarchy_edges) ? snapshot.hierarchy_edges : [])
    .filter((edge) => edge && nodeIds.has(text(edge.source)) && nodeIds.has(text(edge.target)))
    .map((edge) => ({
      id: text(edge.id),
      kind: 'backbone_hierarchy',
      source: text(edge.source),
      target: text(edge.target),
    }))
    .sort((left, right) => compareText(left.id, right.id));
  return { nodes, attachmentEdges, physicalEdges, hierarchyEdges };
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
  const hierarchyEdges = (graph.hierarchyEdges || []).filter(
    (edge) => ids.has(edge.source) && ids.has(edge.target),
  );
  return { ...graph, nodes, hierarchyEdges, attachmentEdges, physicalEdges };
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

/* 所有展开的终端统一排在主干各层之下，按上游设备分组，一组一行。
   早期版本把终端塞进主干行间空隙里避让，结果接入层和终端混成一片，
   横向也跟自己的上级对不上；分层到底反而更好读。 */
function layoutEndpointLayers(positionedBackbone, endpoints, box) {
  const { left, right, startY } = box;
  const available = Math.max(240, right - left);
  const groups = positionedBackbone
    .map((parent) => ({
      parent,
      children: endpoints.filter((node) => node.parentId === parent.id),
    }))
    .filter((group) => group.children.length)
    .sort((left, right) => left.parent.x - right.parent.x);
  const placed = [];
  let y = startY;
  let row = [];
  let used = 0;
  for (const group of groups) {
    for (const packed of packRows(group.children, available)) {
      const need = rowWidth(packed);
      // 只有一两个孩子的组占不满一行，攒到一行里再画，别让它们各占一行拉长画布。
      if (row.length && (used + need) > available) {
        placed.push(...distribute(row, left, right, y));
        y += 74;
        row = [];
        used = 0;
      }
      row.push(...packed);
      used += need + (used ? 40 : 0);
    }
  }
  if (row.length) {
    placed.push(...distribute(row, left, right, y));
    y += 74;
  }
  return { nodes: placed, bottom: groups.length ? y : startY };
}


/* 同层的子设备按父设备在上一层的左右位置排序，同一棵子树因此挨在一起，
   顺着连线往下读不会左右跳。没有父的（多站点各自出口）排在本层最前。 */
function orderByParent(nodes, placed) {
  const parentX = new Map(placed.map((node) => [node.id, node.x]));
  return [...nodes].sort((left, right) => {
    const leftX = parentX.has(left.parentId) ? parentX.get(left.parentId) : -1;
    const rightX = parentX.has(right.parentId) ? parentX.get(right.parentId) : -1;
    return leftX - rightX
      || compareText(left.parentId || '', right.parentId || '')
      || compareText(left.label, right.label)
      || compareText(left.id, right.id);
  });
}

function rowWidth(nodes, gap = 40) {
  return nodes.reduce((total, node) => total + cardWidthFor(node), 0)
    + Math.max(0, nodes.length - 1) * gap;
}

/* 一个场地占一列：列宽取该场地各层里最宽的一行，单场地时正好占满视口。

   终端的兄弟节点会并排在同一行，所以也要算进去，否则一列被压窄后终端只能
   竖着摞成一串。 */
function siteColumnWidth(members, terminals, available) {
  const byTier = new Map();
  for (const node of members) {
    const tier = Number(node.tier ?? 3);
    if (!byTier.has(tier)) byTier.set(tier, []);
    byTier.get(tier).push(node);
  }
  let widest = 260;
  for (const nodes of byTier.values()) {
    for (const row of packRows(nodes, available)) {
      widest = Math.max(widest, rowWidth(row));
    }
  }
  const siblings = new Map();
  for (const node of terminals) {
    const key = node.parentId || '';
    siblings.set(key, (siblings.get(key) || 0) + 1);
  }
  for (const count of siblings.values()) {
    widest = Math.max(widest, Math.min(count, 12) * 132);
  }
  return Math.min(widest, available);
}

export function layoutTopology(graph, viewport = {}) {
  const width = Math.max(320, Number(viewport.width) || 960);
  const height = Math.max(620, Number(viewport.height) || 620);
  const padding = width < 480 ? 48 : 72;
  const backbone = graph.nodes.filter((node) => node.kind === 'backbone');
  const endpoints = graph.nodes.filter((node) => node.kind === 'endpoint');
  const external = graph.nodes.filter((node) => node.kind === 'external');
  const available = width - (padding * 2);

  const siteOf = (node) => (node.siteId === null || node.siteId === undefined
    ? 0 : Number(node.siteId));
  const siteIds = [...new Set(backbone.map(siteOf))].sort((left, right) => left - right);
  const columns = siteIds.map((id) => {
    const members = backbone.filter((node) => siteOf(node) === id);
    return {
      id,
      label: members.find((node) => node.site)?.site || '',
      members,
      width: siteColumnWidth(
        members, endpoints.filter((node) => siteOf(node) === id), available,
      ),
    };
  });
  const gap = columns.length > 1 ? 56 : 0;
  let cursor = padding;
  for (const column of columns) {
    column.left = cursor;
    cursor += column.width + gap;
  }
  const canvasWidth = Math.max(width, cursor - gap + padding);

  const positionedBackbone = [];
  const positionedEndpoints = [];
  const siteFrames = [];
  // 所有场地从同一个顶部开始，并排展开；谁的树更深，画布就跟着长到哪。
  const topY = padding + 46;
  let bottomY = topY;
  for (const column of columns) {
    // 每个场地自己一棵树：从出口往下排到接入，和别的场地互不影响。
    let columnY = topY;
    const members = column.members;
    const tiers = [...new Set(members.map((node) => Number(node.tier ?? 3)))]
      .sort((left, right) => left - right);
    for (const tier of tiers) {
      const layerNodes = orderByParent(
        members.filter((node) => Number(node.tier ?? 3) === tier),
        positionedBackbone,
      );
      for (const row of packRows(layerNodes, column.width)) {
        positionedBackbone.push(
          ...distribute(row, column.left, column.left + column.width, columnY),
        );
        columnY += 118;
      }
      columnY += 35;
    }
    const columnTerminals = endpoints.filter((node) => siteOf(node) === column.id
      || (node.parentId && positionedBackbone.some(
        (parent) => parent.id === node.parentId && siteOf(parent) === column.id,
      )));
    const laid = layoutEndpointLayers(
      positionedBackbone.filter((node) => siteOf(node) === column.id),
      columnTerminals,
      { left: column.left, right: column.left + column.width, startY: columnY + 20 },
    );
    positionedEndpoints.push(...laid.nodes);
    const columnBottom = Math.max(laid.bottom, columnY);
    siteFrames.push({
      id: column.id,
      label: column.label,
      left: column.left,
      right: column.left + column.width,
      top: topY - 34,
      bottom: columnBottom,
    });
    bottomY = Math.max(bottomY, columnBottom);
  }
  const tierY = bottomY;
  // 展开的终端可能超出一屏高度：画布跟着长高，别把它们压回去重叠。
  const contentBottom = Math.max(
    height - padding - 90,
    ...positionedBackbone.map((node) => node.y),
    ...positionedEndpoints.map((node) => node.y),
  );
  const positionedExternal = distribute(
    external, padding, Math.max(width, canvasWidth) - padding, contentBottom + 110,
  );
  const positioned = [...positionedBackbone, ...positionedEndpoints, ...positionedExternal]
    .map((node) => ({
      ...node,
      x: Math.round(clamp(node.x, padding, canvasWidth - padding)),
      y: Math.round(Math.max(node.y, padding)),
    }));
  const contentMaxY = Math.max(
    height - padding,
    ...positioned.map((node) => node.y + 44),
  );

  const placedById = new Map(positioned.map((node) => [node.id, node]));
  const sameTier = (edge) => {
    const source = placedById.get(edge.source);
    const target = placedById.get(edge.target);
    if (!source || !target) return false;
    // 同一层内多行时 y 会差一整行，所以按层号判断，不按 y。
    if (source.kind === 'backbone' && target.kind === 'backbone') {
      return Number(source.tier ?? 3) === Number(target.tier ?? 3);
    }
    return Math.abs(source.y - target.y) < 12;
  };
  return {
    nodes: positioned,
    hierarchyEdges: (graph.hierarchyEdges || []).map((edge) => ({ ...edge })),
    attachmentEdges: graph.attachmentEdges.map((edge) => ({ ...edge })),
    // 同层互联网线在树状图里只会横向切开画面，标出来交给样式淡化。
    physicalEdges: graph.physicalEdges.map((edge) => ({ ...edge, lateral: sameTier(edge) })),
    sites: siteFrames,
    legend: [
      { kind: 'physical', label: 'LLDP/CDP 物理链路' },
      { kind: 'vpn', label: '场地间 VPN' },
      { kind: 'inferred', label: '网段推断终端' },
      { kind: 'external', label: '未解析邻居' },
    ],
    bounds: {
      minX: padding, minY: padding, maxX: canvasWidth - padding, maxY: contentMaxY,
    },
  };
}
