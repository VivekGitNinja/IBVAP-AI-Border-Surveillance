"""
Camera Network Topology Graph & Kinematic Feasibility Engine
============================================================

Models physical CCTV camera nodes, coverage boundaries, directed transition edges,
and ground-path traversal distances across perimeter towers and sectors.

Key Capabilities:
- Graph representation of physical perimeter topology G = (V, E).
- Shortest-path distance computation (Dijkstra algorithm).
- Multi-criteria kinematic feasibility gating (human walking/sprinting vs vehicle kinetics).
- Distinction between overlapping coverage handoffs and blind-zone transitions.
- Clock jitter tolerance buffer (epsilon_clock) across distributed edge nodes.
"""

from __future__ import annotations
import math
import logging
from enum import Enum
from typing import Optional, Any, Dict, List, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class CameraOverlapType(str, Enum):
    """Classification of physical field-of-view overlap between adjacent cameras."""
    OVERLAPPING = "OVERLAPPING"          # Cameras share common visible field of view
    ADJACENT_BLIND = "ADJACENT_BLIND"    # Consecutive cameras separated by unmonitored blind zone
    NON_ADJACENT = "NON_ADJACENT"        # Separated by intermediate cameras or obstacles


@dataclass
class CameraNode:
    """Represents a physical camera tower or edge perception node in site topology."""
    camera_id: int
    name: str
    bop: str
    sector: str
    latitude: float = 0.0
    longitude: float = 0.0
    elevation_meters: float = 0.0
    coverage_radius_meters: float = 50.0
    fov_polygon_gps: list[tuple[float, float]] = field(default_factory=list)
    boundary_exit_headings: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "camera_id": self.camera_id,
            "name": self.name,
            "bop": self.bop,
            "sector": self.sector,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "elevation_meters": self.elevation_meters,
            "coverage_radius_meters": self.coverage_radius_meters,
            "fov_polygon_gps": self.fov_polygon_gps,
            "boundary_exit_headings": self.boundary_exit_headings,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CameraNode:
        return cls(
            camera_id=int(data["camera_id"]),
            name=str(data.get("name", "")),
            bop=str(data.get("bop", "BOP-01")),
            sector=str(data.get("sector", "Sector Alpha")),
            latitude=float(data.get("latitude", 0.0)),
            longitude=float(data.get("longitude", 0.0)),
            elevation_meters=float(data.get("elevation_meters", 0.0)),
            coverage_radius_meters=float(data.get("coverage_radius_meters", 50.0)),
            fov_polygon_gps=data.get("fov_polygon_gps", []),
            boundary_exit_headings=data.get("boundary_exit_headings", {}),
        )


@dataclass
class TopologyEdge:
    """Directed or bidirectional traversal path between two cameras in the topology graph."""
    source_camera_id: int
    target_camera_id: int
    distance_meters: float
    overlap_type: CameraOverlapType = CameraOverlapType.ADJACENT_BLIND
    is_bidirectional: bool = True
    speed_limit_mps: float = 33.3  # Speed limit on this physical segment (default 120 km/h)
    blind_zone_description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_camera_id": self.source_camera_id,
            "target_camera_id": self.target_camera_id,
            "distance_meters": self.distance_meters,
            "overlap_type": self.overlap_type.value if isinstance(self.overlap_type, CameraOverlapType) else str(self.overlap_type),
            "is_bidirectional": self.is_bidirectional,
            "speed_limit_mps": self.speed_limit_mps,
            "blind_zone_description": self.blind_zone_description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TopologyEdge:
        overlap_str = data.get("overlap_type", CameraOverlapType.ADJACENT_BLIND.value)
        try:
            overlap = CameraOverlapType(overlap_str)
        except ValueError:
            overlap = CameraOverlapType.ADJACENT_BLIND
        return cls(
            source_camera_id=int(data["source_camera_id"]),
            target_camera_id=int(data["target_camera_id"]),
            distance_meters=float(data["distance_meters"]),
            overlap_type=overlap,
            is_bidirectional=bool(data.get("is_bidirectional", True)),
            speed_limit_mps=float(data.get("speed_limit_mps", 33.3)),
            blind_zone_description=str(data.get("blind_zone_description", "")),
        )


class CameraTopologyGraph:
    """
    Spatio-temporal network graph representing camera adjacency, path distances,
    and kinematic transition envelopes across perimeter surveillance sectors.
    """

    def __init__(self):
        self._nodes: dict[int, CameraNode] = {}
        self._adj: dict[int, dict[int, TopologyEdge]] = {}

    def add_node(self, node: CameraNode) -> None:
        """Register a camera node in the graph."""
        self._nodes[node.camera_id] = node
        if node.camera_id not in self._adj:
            self._adj[node.camera_id] = {}

    def add_edge(self, edge: TopologyEdge) -> None:
        """Add a traversal edge between two camera nodes."""
        u, v = edge.source_camera_id, edge.target_camera_id
        if u not in self._adj:
            self._adj[u] = {}
        self._adj[u][v] = edge

        if edge.is_bidirectional:
            if v not in self._adj:
                self._adj[v] = {}
            reverse_edge = TopologyEdge(
                source_camera_id=v,
                target_camera_id=u,
                distance_meters=edge.distance_meters,
                overlap_type=edge.overlap_type,
                is_bidirectional=True,
                speed_limit_mps=edge.speed_limit_mps,
                blind_zone_description=edge.blind_zone_description,
            )
            self._adj[v][u] = reverse_edge

    def get_node(self, camera_id: int) -> Optional[CameraNode]:
        """Retrieve node by camera ID."""
        return self._nodes.get(camera_id)

    def get_edge(self, cam_a: int, cam_b: int) -> Optional[TopologyEdge]:
        """Retrieve direct edge between two cameras if it exists."""
        return self._adj.get(cam_a, {}).get(cam_b)

    def get_adjacent_cameras(self, camera_id: int) -> list[int]:
        """Get list of adjacent camera IDs reachable by a direct edge."""
        return list(self._adj.get(camera_id, {}).keys())

    def compute_shortest_path_distance(self, cam_a: int, cam_b: int) -> Optional[float]:
        """
        Compute shortest path distance in meters between two cameras using Dijkstra's algorithm.
        Returns distance in meters, or None if no path exists.
        """
        if cam_a == cam_b:
            return 0.0

        if cam_a not in self._adj or cam_b not in self._adj:
            return None

        # Check direct edge first
        direct = self.get_edge(cam_a, cam_b)
        if direct is not None:
            return direct.distance_meters

        # Dijkstra algorithm
        import heapq
        distances = {cam_a: 0.0}
        pq = [(0.0, cam_a)]
        visited = set()

        while pq:
            d, u = heapq.heappop(pq)
            if u in visited:
                continue
            visited.add(u)

            if u == cam_b:
                return d

            for v, edge in self._adj.get(u, {}).items():
                if v in visited:
                    continue
                new_dist = d + edge.distance_meters
                if new_dist < distances.get(v, float("inf")):
                    distances[v] = new_dist
                    heapq.heappush(pq, (new_dist, v))

        return None

    def evaluate_kinematic_feasibility(
        self,
        cam_a: int,
        cam_b: int,
        delta_t: float,
        class_name: str,
        config: Any,  # CrossCameraConfig
    ) -> tuple[bool, str, float]:
        """
        Evaluate whether a transition from cam_a to cam_b in delta_t seconds
        is physically and kinematically feasible for the given object class.

        Args:
            cam_a: Source camera ID
            cam_b: Target camera ID
            delta_t: Elapsed time (t_entry_b - t_exit_a) in seconds
            class_name: Target object class ("person", "vehicle", "car", etc.)
            config: CrossCameraConfig instance

        Returns:
            (is_feasible, reason_code, effective_velocity_mps)
        """
        if cam_a == cam_b:
            # Same camera: single-camera tracking domain, handled by ByteTrack
            return True, "SAME_CAMERA", 0.0

        # 1. Path existence
        dist = self.compute_shortest_path_distance(cam_a, cam_b)
        if dist is None:
            return False, "TOPOLOGY_DISCONNECTED", 0.0

        edge = self.get_edge(cam_a, cam_b)
        is_overlapping = (edge.overlap_type == CameraOverlapType.OVERLAPPING) if edge else False

        # Class-specific velocity limits from config
        is_vehicle = class_name in ("car", "truck", "bus", "motorcycle", "vehicle")
        if is_vehicle:
            v_max = config.vehicle_max_speed_mps
            v_min = config.vehicle_min_speed_mps
        else:
            v_max = config.person_max_speed_mps
            v_min = config.person_min_speed_mps

        # 2. Overlapping coverage hand-off
        if is_overlapping:
            if config.overlapping_min_dt_seconds <= delta_t <= config.overlapping_max_dt_seconds:
                return True, "OVERLAPPING_HANDOFF_FEASIBLE", 0.0
            elif delta_t < config.overlapping_min_dt_seconds:
                return False, "NEGATIVE_TIME_EXCEEDS_OVERLAP_WINDOW", 0.0
            # If delta_t > overlapping_max_dt_seconds, fall through to blind zone distance checks

        # 3. Blind zone traversal
        adj_dt = delta_t + config.clock_drift_tolerance_seconds

        # Causal violation / impossible teleportation (exceeds clock drift tolerance)
        if adj_dt <= 0.05:
            return False, "CAUSAL_VIOLATION_NEGATIVE_OR_ZERO_TIME", 0.0

        # When delta_t is negative due to clock skew, adj_dt represents the feasible positive transit buffer
        calc_dt = max(0.1, delta_t if delta_t > 0 else adj_dt)
        effective_v = dist / calc_dt

        # Teleportation check: velocity exceeds physical maximum
        if effective_v > v_max:
            return False, f"IMPOSSIBLE_VELOCITY_{effective_v:.1f}_MPS_EXCEEDS_MAX_{v_max:.1f}", effective_v

        # Timeout check: took longer than maximum allowed transit
        if delta_t > config.max_transit_timeout_seconds:
            return False, f"TRANSIT_TIMEOUT_{delta_t:.0f}S_EXCEEDS_MAX_{config.max_transit_timeout_seconds:.0f}S", effective_v

        return True, "KINEMATICALLY_FEASIBLE", effective_v

    def to_dict(self) -> dict[str, Any]:
        """Serialize topology graph to dictionary for persistence/API."""
        nodes = [node.to_dict() for node in self._nodes.values()]
        edges = []
        seen = set()
        for u in self._adj:
            for v, edge in self._adj[u].items():
                edge_id = tuple(sorted([u, v])) if edge.is_bidirectional else (u, v)
                if edge_id not in seen:
                    seen.add(edge_id)
                    edges.append(edge.to_dict())
        return {"nodes": nodes, "edges": edges}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CameraTopologyGraph:
        """Construct topology graph from dictionary."""
        graph = cls()
        for n_dict in data.get("nodes", []):
            graph.add_node(CameraNode.from_dict(n_dict))
        for e_dict in data.get("edges", []):
            graph.add_edge(TopologyEdge.from_dict(e_dict))
        return graph
