import { useEffect, useRef } from "react";
import { DataSet } from "vis-data";
import { Network } from "vis-network";
import "vis-network/styles/vis-network.css";

export default function GraphPanel({ graph, onInspect }) {
  const containerRef = useRef(null);

  useEffect(() => {
    if (!containerRef.current) {
      return undefined;
    }

    const network = new Network(
      containerRef.current,
      {
        nodes: new DataSet(
          graph.nodes.map((node) => ({
            id: node.id,
            label: node.label,
            title: node.kind,
            shape: "dot",
            size: 18,
          })),
        ),
        edges: new DataSet(
          graph.edges.map((edge) => ({
            id: edge.id,
            from: edge.source,
            to: edge.target,
            label: edge.label,
            arrows: "to",
          })),
        ),
      },
      {
        autoResize: true,
        layout: { improvedLayout: true },
        physics: { stabilization: true },
        nodes: {
          color: {
            background: "#f9c74f",
            border: "#203047",
            highlight: { background: "#90be6d", border: "#203047" },
          },
          font: { color: "#132238", face: "Georgia" },
        },
        edges: {
          color: "#4d6a8a",
          font: { align: "middle", color: "#203047" },
        },
      },
    );

    network.on("click", (event) => {
      const [nodeId] = event.nodes;
      const [edgeId] = event.edges;
      if (nodeId) {
        const node = graph.nodes.find((item) => item.id === nodeId);
        if (node) {
          onInspect({ type: "node", ...node });
        }
      } else if (edgeId) {
        const edge = graph.edges.find((item) => item.id === edgeId);
        if (edge) {
          onInspect({ type: "edge", ...edge });
        }
      }
    });

    return () => network.destroy();
  }, [graph, onInspect]);

  return <div className="graph-canvas" ref={containerRef} />;
}
