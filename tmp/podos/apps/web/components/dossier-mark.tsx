/** A small field-note mark shared by navigation and sign-in. */
export function DossierMark() {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      {[0, 1, 2].flatMap((row) => [0, 1, 2].map((col) => (
        <path key={`${row}-${col}`} d={`M${5 + col * 7} ${1 + row * 7}l3 3-3 3-3-3z`} opacity={row + col === 0 ? 0.3 : 1} />
      )))}
    </svg>
  );
}

/** Decorative schematic: source documents converge into a reviewable brief. */
export function ResearchSchematic() {
  return (
    <svg viewBox="0 0 440 300" fill="none" aria-hidden="true" className="h-auto w-full" stroke="currentColor" strokeWidth="0.8">
      {Array.from({ length: 9 }, (_, i) => <path key={`a${i}`} d={`M${30 + i * 35} 100l140 80-140 80`} opacity=".15" />)}
      {Array.from({ length: 9 }, (_, i) => <path key={`b${i}`} d={`M${30 + i * 35} 260L${170 + i * 35} 180 30 100`} opacity=".15" />)}
      <path d="M78 112l55-32 55 32-55 32zM78 112v35l55 32 55-32v-35M133 144v35M251 112l55-32 55 32-55 32zM251 112v35l55 32 55-32v-35M306 144v35" />
      <path d="M133 179v24l88 50 85-50v-24M221 253v-56M221 197l-39-23 39-22 39 22zM182 174v-26l39-23 39 23v26M182 148l39 23 39-23M221 171v26" />
      <path d="M104 112l29-17 29 17-29 17zM277 112l29-17 29 17-29 17z" opacity=".5" />
      {[64, 78, 92].map((y) => <path key={y} d={`M210 ${y}h23`} opacity=".4" />)}
      <path d="M199 50h45v52h-45zM221 102v23" />
      <circle cx="133" cy="203" r="3" fill="currentColor" /><circle cx="306" cy="203" r="3" fill="currentColor" /><circle cx="221" cy="253" r="3" fill="currentColor" />
    </svg>
  );
}
