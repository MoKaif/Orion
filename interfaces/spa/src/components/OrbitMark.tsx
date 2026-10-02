import "./orbit-mark.css";

export default function OrbitMark({ size = 32 }: { size?: number }) {
  return (
    <span className="orbit-mark" style={{ width: size, height: size }} aria-hidden="true">
      <span className="orbit-core" />
      <span className="orbit-ring" />
      <span className="orbit-moon" />
    </span>
  );
}
