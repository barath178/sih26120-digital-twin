import { useMemo, useRef } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import * as THREE from "three";
import type { Phase } from "../types";

export interface SceneProps {
  phase: Phase;
  spm: number;
  stroke: number;
  rh: number;          // heated-zone radius, m
  tAvg: number;        // heated-zone temperature, C
  mu: number;          // oil viscosity, cP
  pumpDepth: number;   // m
  depth: number;       // mid-perforation depth, m
  fillage: number;
  steamRate: number;
  running: boolean;
}

const DEPTH_UNITS = 8.5;       // visual depth of mid-perforation (compressed)
const H_SCALE = 0.3;          // visual units per metre of heated-zone radius
const RES_THICK = 1.5;
const LAMBDA = 0.28;

function tempColor(t: number) {
  const stops: [number, string][] = [[47, "#7d6a4d"], [90, "#a0762c"], [150, "#c98500"], [220, "#d95926"], [320, "#e66767"]];
  const c = new THREE.Color();
  for (let i = 0; i < stops.length - 1; i++) {
    const [t0, c0] = stops[i];
    const [t1, c1] = stops[i + 1];
    if (t <= t1 || i === stops.length - 2) {
      const f = Math.min(Math.max((t - t0) / (t1 - t0), 0), 1);
      return c.set(c0).lerp(new THREE.Color(c1), f);
    }
  }
  return c.set(stops[0][1]);
}

/** Cylinder oriented between two points (for pitman arms, rods). */
function setBetween(mesh: THREE.Mesh, a: THREE.Vector3, b: THREE.Vector3) {
  const mid = a.clone().add(b).multiplyScalar(0.5);
  const dir = b.clone().sub(a);
  const len = dir.length();
  mesh.position.copy(mid);
  mesh.scale.set(1, len, 1);
  mesh.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir.normalize());
}

function Pumpjack({ props, strokePos }: { props: SceneProps; strokePos: { current: number } }) {
  const beam = useRef<THREE.Group>(null);
  const crank = useRef<THREE.Group>(null);
  const pitmanL = useRef<THREE.Mesh>(null);
  const pitmanR = useRef<THREE.Mesh>(null);
  const bridle = useRef<THREE.Mesh>(null);
  const rodTop = useRef<THREE.Mesh>(null);
  const theta = useRef(0);
  const pivot = new THREE.Vector3(-2.2, 4.2, 0);
  const crankC = new THREE.Vector3(-4.3, 1.7, 0);
  const phiMax = 0.32;

  useFrame((_, dt) => {
    const running = props.running && props.phase === "production" && props.spm > 0.1;
    if (running) theta.current = (theta.current + dt * (props.spm * 2 * Math.PI) / 60) % (2 * Math.PI);
    const th = theta.current;
    // normalised polished-rod position (0 bottom, 1 top), conventional-unit kinematics
    const p = ((1 - Math.cos(th)) + (LAMBDA / 2) * (1 - Math.cos(2 * th))) / 2;
    strokePos.current = p;
    const phi = (p - 0.5) * 2 * phiMax;
    if (beam.current) beam.current.rotation.z = phi;
    if (crank.current) crank.current.rotation.z = -th;
    const tail = pivot.clone().add(new THREE.Vector3(-2.0 * Math.cos(phi), -2.0 * Math.sin(phi), 0));
    const pin = crankC.clone().add(new THREE.Vector3(0.95 * Math.cos(-th + Math.PI / 2), 0.95 * Math.sin(-th + Math.PI / 2), 0));
    if (pitmanL.current) setBetween(pitmanL.current, pin.clone().setZ(0.45), tail.clone().setZ(0.3));
    if (pitmanR.current) setBetween(pitmanR.current, pin.clone().setZ(-0.45), tail.clone().setZ(-0.3));
    const headY = pivot.y + 2.45 * Math.sin(phi);
    const clampY = headY - 1.9;
    if (bridle.current) setBetween(bridle.current, new THREE.Vector3(0.02, headY, 0), new THREE.Vector3(0.02, clampY, 0));
    if (rodTop.current) setBetween(rodTop.current, new THREE.Vector3(0, clampY, 0), new THREE.Vector3(0, 0.9, 0));
  });

  const steel = "#8f8e86";
  const paint = "#c98500";
  return (
    <group>
      {/* skid */}
      <mesh position={[-3, 0.1, 0]}><boxGeometry args={[6, 0.2, 1.6]} /><meshStandardMaterial color="#3a3a36" /></mesh>
      {/* samson post */}
      {[0.45, -0.45].map((z) => (
        <mesh key={z} position={[-2.2, 2.15, z]} rotation={[z > 0 ? 0.12 : -0.12, 0, 0]}><boxGeometry args={[0.18, 4.2, 0.18]} /><meshStandardMaterial color={paint} /></mesh>
      ))}
      {/* walking beam + horsehead */}
      <group ref={beam} position={pivot.toArray()}>
        <mesh position={[0.2, 0, 0]}><boxGeometry args={[4.6, 0.28, 0.3]} /><meshStandardMaterial color={paint} /></mesh>
        <mesh position={[2.45, -0.45, 0]}><cylinderGeometry args={[1.0, 1.0, 0.32, 24, 1, false, -0.5, 1.5]} /><meshStandardMaterial color={paint} side={THREE.DoubleSide} /></mesh>
        <mesh position={[-2.0, 0, 0]}><boxGeometry args={[0.3, 0.4, 0.8]} /><meshStandardMaterial color={steel} /></mesh>
      </group>
      {/* gearbox, crank and counterweights */}
      <mesh position={[crankC.x, 1.1, 0]}><boxGeometry args={[1.2, 1.4, 1.1]} /><meshStandardMaterial color="#52514e" /></mesh>
      <group ref={crank} position={crankC.toArray()}>
        {[0.62, -0.62].map((z) => (
          <group key={z} position={[0, 0, z]}>
            <mesh position={[0, 0.45, 0]}><boxGeometry args={[0.3, 1.2, 0.12]} /><meshStandardMaterial color={steel} /></mesh>
            <mesh position={[0, -0.55, 0]}><boxGeometry args={[1.1, 0.7, 0.2]} /><meshStandardMaterial color="#3987e5" /></mesh>
          </group>
        ))}
      </group>
      <mesh ref={pitmanL}><cylinderGeometry args={[0.05, 0.05, 1, 8]} /><meshStandardMaterial color={steel} /></mesh>
      <mesh ref={pitmanR}><cylinderGeometry args={[0.05, 0.05, 1, 8]} /><meshStandardMaterial color={steel} /></mesh>
      {/* motor */}
      <mesh position={[-5.5, 0.6, 0]}><boxGeometry args={[0.9, 0.8, 0.8]} /><meshStandardMaterial color="#256abf" /></mesh>
      {/* bridle + polished rod */}
      <mesh ref={bridle}><cylinderGeometry args={[0.025, 0.025, 1, 6]} /><meshStandardMaterial color="#c3c2b7" /></mesh>
      <mesh ref={rodTop}><cylinderGeometry args={[0.035, 0.035, 1, 8]} /><meshStandardMaterial color="#e5e4de" metalness={0.6} roughness={0.3} /></mesh>
      {/* wellhead / stuffing box */}
      <mesh position={[0, 0.45, 0]}><cylinderGeometry args={[0.22, 0.28, 0.9, 16]} /><meshStandardMaterial color={props.phase === "injection" ? "#d95926" : "#6b6a64"} /></mesh>
      <mesh position={[0.55, 0.55, 0]} rotation={[0, 0, Math.PI / 2]}><cylinderGeometry args={[0.07, 0.07, 0.8, 8]} /><meshStandardMaterial color="#6b6a64" /></mesh>
    </group>
  );
}

function Subsurface({ props, strokePos }: { props: SceneProps; strokePos: { current: number } }) {
  const zone = useRef<THREE.Mesh>(null);
  const zoneMat = useRef<THREE.MeshStandardMaterial>(null);
  const rods = useRef<THREE.Mesh>(null);
  const plunger = useRef<THREE.Mesh>(null);
  const particles = useRef<THREE.InstancedMesh>(null);
  const cur = useRef({ r: props.rh * H_SCALE, t: props.tAvg });
  const resTop = -DEPTH_UNITS + RES_THICK / 2;
  const pumpY = -(props.pumpDepth / props.depth) * DEPTH_UNITS;
  const N = 40;
  const dummy = useMemo(() => new THREE.Object3D(), []);
  const seeds = useMemo(() => Array.from({ length: N }, (_, i) => i / N), []);
  const strokeVis = 0.5;

  useFrame((state, dt) => {
    const c = cur.current;
    c.r += (Math.max(props.rh * H_SCALE, 0.02) - c.r) * Math.min(dt * 1.5, 1);
    c.t += (props.tAvg - c.t) * Math.min(dt * 1.5, 1);
    if (zone.current) {
      zone.current.scale.set(Math.max(c.r, 0.02), 1, Math.max(c.r, 0.02));
      zone.current.visible = props.rh > 0.05;
    }
    if (zoneMat.current) {
      const col = tempColor(c.t);
      zoneMat.current.color.copy(col);
      zoneMat.current.emissive.copy(col);
      zoneMat.current.emissiveIntensity = Math.min(Math.max((c.t - 60) / 250, 0), 0.9);
      zoneMat.current.opacity = 0.35 + 0.4 * Math.min(Math.max((c.t - 47) / 250, 0), 1);
    }
    const off = (strokePos.current - 0.5) * strokeVis;
    if (rods.current) rods.current.position.y = (0.9 + pumpY) / 2 + off;
    if (plunger.current) plunger.current.position.y = pumpY + 0.15 + off;
    if (particles.current) {
      const injecting = props.phase === "injection";
      particles.current.visible = injecting;
      if (injecting) {
        const t = state.clock.elapsedTime;
        seeds.forEach((s, i) => {
          const f = (s + t * 0.25) % 1;
          dummy.position.set(Math.sin(i * 2.4) * 0.05, -f * (DEPTH_UNITS - 0.5), Math.cos(i * 2.4) * 0.05);
          const sc = 0.06 + 0.03 * Math.sin(i + t * 3);
          dummy.scale.set(sc, sc, sc);
          dummy.updateMatrix();
          particles.current!.setMatrixAt(i, dummy.matrix);
        });
        particles.current.instanceMatrix.needsUpdate = true;
      }
    }
  });

  const strata: [number, number, string, string][] = [
    [0, -2.4, "#4d4636", "Alluvium / Tertiary"],
    [-2.4, -5, "#443f33", "Shale & siltstone"],
    [-5, resTop, "#3a362d", "Cap rock (overburden)"],
  ];
  return (
    <group>
      {strata.map(([y0, y1, color, label]) => (
        <group key={label}>
          <mesh position={[1.5, (y0 + y1) / 2, -2.2]}><boxGeometry args={[13, y0 - y1, 0.1]} /><meshStandardMaterial color={color} /></mesh>
        </group>
      ))}
      {/* reservoir */}
      <mesh position={[1.5, -DEPTH_UNITS, -1.2]}><boxGeometry args={[13, RES_THICK, 2.1]} /><meshStandardMaterial color="#7d6a4d" transparent opacity={0.55} /></mesh>
      <mesh position={[1.5, -DEPTH_UNITS - RES_THICK / 2 - 0.6, -2.2]}><boxGeometry args={[14, 1.2, 0.1]} /><meshStandardMaterial color="#2a2824" /></mesh>
      {/* heated zone */}
      <mesh ref={zone} position={[0, -DEPTH_UNITS, 0]}>
        <cylinderGeometry args={[1, 1, RES_THICK * 0.96, 48]} />
        <meshStandardMaterial ref={zoneMat} transparent depthWrite={false} />
      </mesh>
      {/* casing, tubing, rods, pump */}
      <mesh position={[0, -DEPTH_UNITS / 2 - 0.2, 0]}><cylinderGeometry args={[0.2, 0.2, DEPTH_UNITS + 0.4, 20, 1, true]} /><meshStandardMaterial color="#8f8e86" transparent opacity={0.28} side={THREE.DoubleSide} /></mesh>
      <mesh position={[0, pumpY / 2, 0]}><cylinderGeometry args={[0.09, 0.09, -pumpY, 12, 1, true]} /><meshStandardMaterial color="#c3c2b7" transparent opacity={0.35} side={THREE.DoubleSide} /></mesh>
      <mesh ref={rods}><cylinderGeometry args={[0.03, 0.03, 0.9 - pumpY, 8]} /><meshStandardMaterial color="#e5e4de" /></mesh>
      <mesh position={[0, pumpY, 0]}><cylinderGeometry args={[0.12, 0.12, 0.9, 16]} /><meshStandardMaterial color="#3987e5" transparent opacity={0.6} /></mesh>
      <mesh ref={plunger}><cylinderGeometry args={[0.1, 0.1, 0.3, 16]} /><meshStandardMaterial color="#e5e4de" /></mesh>
      {/* barrel fill level */}
      <mesh position={[0, pumpY - 0.45 + 0.45 * Math.min(props.fillage, 1), 0]}>
        <cylinderGeometry args={[0.115, 0.115, 0.9 * Math.max(Math.min(props.fillage, 1), 0.02), 16]} />
        <meshStandardMaterial color="#c98500" transparent opacity={0.5} />
      </mesh>
      <instancedMesh ref={particles} args={[undefined, undefined, N]}>
        <sphereGeometry args={[1, 8, 8]} />
        <meshStandardMaterial color="#ffd0b0" emissive="#d95926" emissiveIntensity={0.8} />
      </instancedMesh>
    </group>
  );
}

export default function WellScene3D(props: SceneProps) {
  const strokePos = useRef(0);
  return (
    <div className="relative h-full w-full">
      <Canvas camera={{ position: [10, -1.4, 19.5], fov: 45 }} dpr={[1, 2]}>
        <color attach="background" args={["#171512"]} />
        <ambientLight intensity={0.8} />
        <directionalLight position={[8, 12, 6]} intensity={1.2} />
        <pointLight position={[0, -DEPTH_UNITS + 1, 2]} intensity={props.tAvg > 100 ? 6 : 1} color="#ff9a5a" distance={8} />
        {/* ground (translucent so the section below is visible) */}
        <mesh rotation={[-Math.PI / 2, 0, 0]} position={[3, 0, 0]}>
          <planeGeometry args={[18, 8]} />
          <meshStandardMaterial color="#4a4436" transparent opacity={0.35} side={THREE.DoubleSide} />
        </mesh>
        <Pumpjack props={props} strokePos={strokePos} />
        <Subsurface props={props} strokePos={strokePos} />
        <OrbitControls target={[0, -2.2, 0]} enablePan maxDistance={34} minDistance={6} />
      </Canvas>
    </div>
  );
}
