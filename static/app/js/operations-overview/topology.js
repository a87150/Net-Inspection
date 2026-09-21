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

function distribute(nodes, minX, maxX, y) {
  if (!nodes.length) return [];
  const step = nodes.length === 1 ? 0 : (maxX - minX) / (nodes.length - 1);
  const start = nodes.length === 1 ? (minX + maxX) / 2 : minX;
  return nodes.map((node, index) => ({ ...node, x: start + (step * index), y }));
}

export function layoutTopology(graph, viewport = {}) {
  const width = Math.max(320, Number(viewport.width) || 960);
  const height = Math.max(620, Number(viewport.height) || 620);
  const padding = width < 480 ? 48 : 72;
  const backbone = graph.nodes.filter((node) => node.kind === 'backbone');
  const endpoints = graph.nodes.filter((node) => node.kind === 'endpoint');
  const external = graph.nodes.filter((node) => node.kind === 'external');
  const tiers = [...new Set(backbone.map((node) => Number(node.tier ?? 3)))].sort((a, b) => a - b);
  const tierTop = 70;
  const tierGap = tiers.length > 1 ? Math.min(140, (height - 210) / (tiers.length - 1)) : 0;
  const positionedBackbone = tiers.flatMap((tier, tierIndex) => distribute(
    backbone.filter((node) => Number(node.tier ?? 3) === tier),
    padding, width - padding, tierTop + (tierIndex * tierGap),
  ));
  const positions = new Map(positionedBackbone.map((node) => [node.id, node]));
  const positionedEndpoints = [];
  for (const parent of positionedBackbone) {
    const children = endpoints.filter((node) => node.parentId === parent.id);
    const childGap = Math.min(112, Math.max(74, (width - (padding * 2)) / Math.max(1, children.length)));
    const totalWidth = (children.length - 1) * childGap;
    children.forEach((node, index) => positionedEndpoints.push({
      ...node,
      x: clamp(parent.x - (totalWidth / 2) + (index * childGap), padding, width - padding),
      y: clamp(parent.y + 88, padding, height - padding),
    }));
  }
  const positionedExternal = distribute(external, padding, width - padding, height - padding);
  const positioned = [...positionedBackbone, ...positionedEndpoints, ...positionedExternal]
    .map((node) => ({
      ...node,
      x: Math.round(clamp(node.x, padding, width - padding)),
      y: Math.round(clamp(node.y, padding, height - padding)),
    }));

  return {
    nodes: positioned,
    attachmentEdges: graph.attachmentEdges.map((edge) => ({ ...edge })),
    physicalEdges: graph.physicalEdges.map((edge) => ({ ...edge })),
    legend: [
      { kind: 'physical', label: 'LLDP/CDP 物理链路' },
      { kind: 'inferred', label: '网段推断终端' },
      { kind: 'external', label: '未解析邻居' },
    ],
    bounds: { minX: padding, minY: padding, maxX: width - padding, maxY: height - padding },
  };
}
