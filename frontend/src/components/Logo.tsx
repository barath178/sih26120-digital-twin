/** Brand mark: a nodding-donkey pump on a heat-orange tile. */
export default function Logo({ size = 32 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" role="img" aria-label="Baghewala Twin">
      <rect width="32" height="32" rx="8" fill="#f08a3c" />
      <g fill="none" stroke="#1a0f05" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M11 24.5 16 12.5 21 24.5" />
        <path d="M6 13.5 24 9" strokeWidth="2.4" />
        <path d="M24 9c3 .6 3.4 3.4 2.6 5.6" />
        <path d="M25.6 14.6V24.5" strokeWidth="1.6" />
        <path d="M5 24.5H27" />
      </g>
    </svg>
  );
}
