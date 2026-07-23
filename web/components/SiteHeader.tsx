"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

type Props = {
  onOpenWaitlist?: () => void;
  signedIn?: boolean;
};

export function SiteHeader({ onOpenWaitlist, signedIn }: Props) {
  const pathname = usePathname();
  const onPricing = pathname === "/pricing";
  const onLogin = pathname === "/login";
  const onGuide = pathname === "/guide";
  const onHome = !onPricing && !onLogin && !onGuide;

  return (
    <header className="lp-header">
      <div className="lp-header-inner">
        <Link className="lp-logo" href="/" aria-label="Z home">
          <span className="lp-logo-mark" aria-hidden="true">
            Z
          </span>
          <span className="lp-logo-word">Z</span>
        </Link>
        <nav className="lp-nav" aria-label="Primary">
          {onHome ? (
            <>
              <a href="#why">Why</a>
              <a href="#how">How it works</a>
              <a href="#skills">Skills</a>
              <a href="#detectors">Detectors</a>
              <a href="#install">Try it</a>
            </>
          ) : (
            <>
              <Link href="/#why">Why</Link>
              <Link href="/#how">How it works</Link>
              <Link href="/#skills">Skills</Link>
              <Link href="/#detectors">Detectors</Link>
              <Link href="/#install">Try it</Link>
            </>
          )}
          <Link href="/guide" className={onGuide ? "lp-nav-active" : undefined}>
            Guide
          </Link>
          <Link href="/pricing" className={onPricing ? "lp-nav-active" : undefined}>
            Pricing
          </Link>
          {signedIn ? (
            <a href="/app/integrations">App</a>
          ) : (
            <Link href="/login" className={onLogin ? "lp-nav-active" : undefined}>
              Sign in
            </Link>
          )}
        </nav>
        <button
          type="button"
          className="lp-btn lp-btn-sm"
          onClick={() => onOpenWaitlist?.()}
        >
          Join waitlist
        </button>
      </div>
    </header>
  );
}
