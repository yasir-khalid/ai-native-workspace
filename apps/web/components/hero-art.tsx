/**
 * The sign-in hero: a cluster of soft vertical light bars over an indigo glow, dissolving into a
 * near-black base. Pure SVG/CSS — no images — so it scales crisply from phone to wide display.
 */

const BARS: { x: number; y: number; h: number; o: number }[] = [
  { x: 433, y: 392, h: 448, o: 0.52 },
  { x: 487, y: 312, h: 528, o: 0.68 },
  { x: 541, y: 430, h: 410, o: 0.48 },
  { x: 595, y: 208, h: 632, o: 0.82 },
  { x: 649, y: 292, h: 548, o: 0.64 },
  { x: 703, y: 104, h: 736, o: 1 },
  { x: 757, y: 256, h: 584, o: 0.72 },
  { x: 811, y: 168, h: 672, o: 0.9 },
  { x: 865, y: 360, h: 480, o: 0.56 },
  { x: 919, y: 262, h: 578, o: 0.7 },
  { x: 973, y: 420, h: 420, o: 0.48 },
];

const GRAIN =
  "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='140' height='140'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E\")";

export function HeroArt() {
  return (
    <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden bg-[#05050a]">
      <div
        className="absolute inset-0"
        style={{
          background:
            "radial-gradient(74% 58% at 50% 2%, rgba(126,142,255,.52), rgba(126,142,255,0) 58%)," +
            "radial-gradient(58% 46% at 50% 20%, rgba(90,102,214,.40), rgba(90,102,214,0) 66%)," +
            "radial-gradient(46% 36% at 76% 30%, rgba(226,3,152,.12), rgba(226,3,152,0) 72%)," +
            "radial-gradient(42% 34% at 24% 26%, rgba(25,229,192,.08), rgba(25,229,192,0) 74%)",
        }}
      />
      <svg className="absolute inset-0 h-full w-full" viewBox="0 0 1440 760" preserveAspectRatio="xMidYMid slice">
        <defs>
          <linearGradient id="hero-bar" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#eef3ff" stopOpacity=".98" />
            <stop offset=".20" stopColor="#ccd6ff" stopOpacity=".66" />
            <stop offset=".48" stopColor="#8593ff" stopOpacity=".24" />
            <stop offset="1" stopColor="#05050a" stopOpacity="0" />
          </linearGradient>
          <filter id="hero-soft" x="-30%" y="-30%" width="160%" height="160%">
            <feGaussianBlur stdDeviation="11" />
          </filter>
        </defs>
        <g filter="url(#hero-soft)">
          {BARS.map((b, i) => (
            <rect key={i} x={b.x} y={b.y} width={34} height={b.h} rx={17} fill="url(#hero-bar)" opacity={b.o} />
          ))}
        </g>
      </svg>
      <div className="absolute inset-x-0 bottom-0 h-[70%] bg-gradient-to-b from-transparent via-[#05050a]/50 to-[#05050a]" />
      <div
        className="absolute inset-0"
        style={{ background: "radial-gradient(130% 100% at 50% 38%, rgba(0,0,0,0) 42%, rgba(5,5,10,.7) 100%)" }}
      />
      <div className="absolute inset-0 opacity-10 mix-blend-overlay" style={{ backgroundImage: GRAIN }} />
    </div>
  );
}
