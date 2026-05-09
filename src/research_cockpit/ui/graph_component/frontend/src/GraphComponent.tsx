import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import dagre from "dagre";
import {
  Background,
  Controls,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  type Edge,
  type Node,
  type NodeMouseHandler
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  Streamlit,
  withStreamlitConnection,
  type ComponentProps
} from "streamlit-component-lib";

type GraphNode = {
  id: string;
  label: string;
  title?: string;
  type?: string;
  status?: string;
  priority?: string;
  color?: string;
  is_current_focus?: boolean;
  is_focus?: boolean;
  badges?: string[];
  effective_baseline_option_id?: string;
};

type GraphEdge = {
  id: string;
  source: string;
  target: string;
  label?: string | null;
  type?: string | null;
  color?: string;
  dashes?: boolean;
  width?: number;
};

type GraphPayload = {
  nodes?: GraphNode[];
  edges?: GraphEdge[];
  selected_node_id?: string | null;
};

const nodeWidth = 220;
const nodeHeight = 96;
const structuralEdgeTypes = new Set(["parent", "contains", "child"]);
const selectionOnlyEdgeTypes = new Set(["baseline_use"]);

function labelFor(node: GraphNode) {
  const badges = node.badges || [];
  return (
    <div className="graph-node-label" title={node.title || node.label}>
      <div className="graph-node-title">{node.label}</div>
      {badges.length > 0 ? (
        <div className="graph-node-badges" aria-label="Baseline markers">
          {badges.map((badge) => (
            <span
              key={badge}
              className={badge === "CURRENT BASELINE" ? "is-current-baseline" : undefined}
            >
              {badge}
            </span>
          ))}
        </div>
      ) : null}
      <div className="graph-node-meta">
        <span>{node.type || "node"}</span>
        <span>{node.status || "unknown"}</span>
        {node.priority ? <span>{node.priority}</span> : null}
      </div>
    </div>
  );
}

function nodeBorder(node: GraphNode, selectedNodeId: string | null) {
  if (node.id === selectedNodeId) {
    return "#111827";
  }
  if (node.is_current_focus) {
    return "#D93025";
  }
  if (node.is_focus) {
    return "#F59E0B";
  }
  return "#6B7280";
}

function isStructuralEdge(edge: GraphEdge) {
  return structuralEdgeTypes.has(String(edge.type || "").toLowerCase());
}

function isSelectionOnlyEdge(edge: GraphEdge) {
  return selectionOnlyEdgeTypes.has(String(edge.type || "").toLowerCase());
}

function layoutEdgesFor(payload: GraphPayload) {
  const edges = payload.edges || [];
  const structuralEdges = edges.filter(isStructuralEdge);
  return structuralEdges.length > 0 ? structuralEdges : edges.filter((edge) => !isSelectionOnlyEdge(edge));
}

function layoutSignature(payload: GraphPayload) {
  return JSON.stringify({
    nodes: (payload.nodes || []).map((node) => node.id),
    edges: layoutEdgesFor(payload).map((edge) => [
      edge.source,
      edge.target,
      edge.type
    ])
  });
}

function renderSignature(payload: GraphPayload) {
  return JSON.stringify({
    nodes: (payload.nodes || []).map((node) => [
      node.id,
      node.label,
      node.type,
      node.status,
      node.priority,
      node.color,
      node.title,
      node.is_current_focus,
      node.is_focus,
      node.badges,
      node.effective_baseline_option_id
    ]),
    edges: (payload.edges || []).map((edge) => [
      edge.id,
      edge.source,
      edge.target,
      edge.type,
      edge.label,
      edge.color,
      edge.dashes,
      edge.width
    ])
  });
}

function toReactFlowNodes(payload: GraphPayload, selectedNodeId: string | null): Node[] {
  return (payload.nodes || []).map((node) => ({
    id: node.id,
    position: { x: 0, y: 0 },
    sourcePosition: Position.Right,
    targetPosition: Position.Left,
    data: { label: labelFor(node) },
    selected: node.id === selectedNodeId,
    style: {
      width: nodeWidth,
      minHeight: 76,
      borderRadius: 8,
      borderWidth: node.id === selectedNodeId || node.is_current_focus ? 3 : 1,
      borderColor: nodeBorder(node, selectedNodeId),
      background: node.color || "#F9FAFB",
      color: "#111827",
      padding: 10
    }
  }));
}

function layoutNodes(nodes: Node[], edges: GraphEdge[]): Node[] {
  if (nodes.length === 0) {
    return nodes;
  }

  const layout = new dagre.graphlib.Graph();
  layout.setDefaultEdgeLabel(() => ({}));
  layout.setGraph({
    rankdir: "LR",
    align: "UL",
    nodesep: 44,
    ranksep: 116,
    marginx: 24,
    marginy: 24
  });

  nodes.forEach((node) => {
    layout.setNode(node.id, { width: nodeWidth, height: nodeHeight });
  });

  const structuralEdges = edges.filter(isStructuralEdge);
  const layoutEdges = structuralEdges.length > 0 ? structuralEdges : edges;
  layoutEdges.forEach((edge) => {
    if (layout.hasNode(edge.source) && layout.hasNode(edge.target)) {
      layout.setEdge(edge.source, edge.target);
    }
  });

  dagre.layout(layout);

  return nodes.map((node, index) => {
    const positioned = layout.node(node.id);
    if (!positioned) {
      return { ...node, position: { x: index * (nodeWidth + 70), y: 0 } };
    }
    return {
      ...node,
      position: {
        x: positioned.x - nodeWidth / 2,
        y: positioned.y - nodeHeight / 2
      }
    };
  });
}

function baselineUseEdgeFor(payload: GraphPayload, selectedNodeId: string | null): GraphEdge | null {
  if (!selectedNodeId) {
    return null;
  }
  const nodes = payload.nodes || [];
  const selectedNode = nodes.find((node) => node.id === selectedNodeId);
  const source = selectedNode?.effective_baseline_option_id || "";
  if (!source || source === selectedNodeId || !nodes.some((node) => node.id === source)) {
    return null;
  }
  return {
    id: `baseline_use--${source}--${selectedNodeId}`,
    source,
    target: selectedNodeId,
    label: "uses baseline",
    type: "baseline_use",
    color: "#64748B",
    dashes: true,
    width: 1.3
  };
}

function visibleGraphEdges(payload: GraphPayload, selectedNodeId: string | null): GraphEdge[] {
  const edges = (payload.edges || []).filter((edge) => !isSelectionOnlyEdge(edge));
  const baselineUseEdge = baselineUseEdgeFor(payload, selectedNodeId);
  return baselineUseEdge ? [...edges, baselineUseEdge] : edges;
}

function toReactFlowEdges(edges: GraphEdge[]): Edge[] {
  return edges.map((edge) => {
    const structural = isStructuralEdge(edge);
    const stroke = structural ? "#2563EB" : edge.color || "#6B7280";
    return {
      id: edge.id,
      source: edge.source,
      target: edge.target,
      label: edge.label || undefined,
      type: "smoothstep",
      markerEnd: { type: MarkerType.ArrowClosed, color: stroke, width: 24, height: 24 },
      style: {
        stroke,
        strokeWidth: Math.max(edge.width || 1, structural ? 2.4 : 1.4),
        strokeDasharray: edge.dashes ? "6 4" : undefined
      },
      labelStyle: { fill: "#374151", fontSize: 11 },
      zIndex: structural ? 2 : 1
    };
  });
}

function ResearchGraph({ args }: ComponentProps) {
  const payload = (args.payload || {}) as GraphPayload;
  const selectedNodeId = (args.selected_node_id || payload.selected_node_id || null) as string | null;
  const [visualSelectedNodeId, setVisualSelectedNodeId] = useState<string | null>(selectedNodeId);
  const layoutKey = useMemo(() => layoutSignature(payload), [payload]);
  const renderKey = useMemo(() => renderSignature(payload), [payload]);
  const nodePositions = useMemo(() => {
    const flowNodes = toReactFlowNodes(payload, null);
    return new Map(layoutNodes(flowNodes, layoutEdgesFor(payload)).map((node) => [node.id, node.position]));
  }, [layoutKey]);
  const graphEdges = useMemo(() => visibleGraphEdges(payload, visualSelectedNodeId), [renderKey, visualSelectedNodeId]);
  const baseEdges = useMemo(() => toReactFlowEdges(graphEdges), [graphEdges]);
  const baseNodes = useMemo(() => {
    const flowNodes = toReactFlowNodes(payload, visualSelectedNodeId);
    return flowNodes.map((node, index) => ({
      ...node,
      position: nodePositions.get(node.id) || { x: index * (nodeWidth + 70), y: 0 }
    }));
  }, [nodePositions, renderKey, visualSelectedNodeId]);

  const [nodes, setNodes, onNodesChange] = useNodesState(baseNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(baseEdges);
  const previousLayoutKey = useRef<string>("");
  const pendingSelectedNodeId = useRef<string | null>(null);
  const clickSequence = useRef<number>(0);

  useEffect(() => {
    setEdges(baseEdges);
  }, [baseEdges, setEdges]);

  useEffect(() => {
    const pending = pendingSelectedNodeId.current;
    if (pending) {
      if (selectedNodeId === pending) {
        pendingSelectedNodeId.current = null;
        setVisualSelectedNodeId(selectedNodeId);
      }
      return;
    }
    setVisualSelectedNodeId(selectedNodeId);
  }, [selectedNodeId]);

  useEffect(() => {
    if (previousLayoutKey.current !== layoutKey) {
      previousLayoutKey.current = layoutKey;
      setNodes(baseNodes);
      return;
    }

    setNodes((currentNodes) => {
      const currentById = new Map(currentNodes.map((node) => [node.id, node]));
      return baseNodes.map((node) => {
        const current = currentById.get(node.id);
        return current ? { ...node, position: current.position } : node;
      });
    });
  }, [baseNodes, layoutKey, setNodes]);

  useEffect(() => {
    Streamlit.setFrameHeight(660);
  }, [nodes.length, edges.length]);

  const onNodeClick = useCallback<NodeMouseHandler>((_event, node) => {
    clickSequence.current += 1;
    pendingSelectedNodeId.current = node.id;
    setVisualSelectedNodeId(node.id);
    Streamlit.setComponentValue({
      selected_node_id: node.id,
      event_type: "node_click",
      event_id: `${Date.now()}-${clickSequence.current}-${node.id}`
    });
  }, []);

  if (nodes.length === 0) {
    return <div className="graph-empty" role="status">No graph nodes match the current filters.</div>;
  }

  return (
    <div className="graph-shell">
      <ReactFlowProvider>
        <ReactFlow
          key={layoutKey}
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeClick={onNodeClick}
          fitView
          fitViewOptions={{ padding: 0.16 }}
          minZoom={0.18}
          maxZoom={2.5}
          nodesDraggable
          nodesConnectable={false}
          elementsSelectable
          panOnScroll
        >
          <Background gap={22} color="#E5E7EB" />
          <MiniMap pannable zoomable nodeStrokeWidth={3} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </ReactFlowProvider>
    </div>
  );
}

export default withStreamlitConnection(ResearchGraph);
