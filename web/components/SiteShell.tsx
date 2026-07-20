"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { SiteHeader } from "@/components/SiteHeader";
import { WaitlistModal } from "@/components/WaitlistModal";
import { isSignedIn } from "@/lib/auth";

type Props = {
  children: React.ReactNode;
  initialInterest?: string;
};

export function SiteShell({ children, initialInterest = "" }: Props) {
  const [waitlistOpen, setWaitlistOpen] = useState(false);
  const [interest, setInterest] = useState(initialInterest);
  const [signedIn, setSignedIn] = useState(false);

  useEffect(() => {
    setSignedIn(isSignedIn());
  }, []);

  useEffect(() => {
    const sections = document.querySelectorAll(".lp-section, .lp-hero");
    if (!sections.length) return;
    if (!("IntersectionObserver" in window)) {
      sections.forEach((s) => s.classList.add("lp-revealed"));
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("lp-revealed");
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.15 }
    );
    sections.forEach((s) => observer.observe(s));
    return () => observer.disconnect();
  }, []);

  const openWaitlist = useCallback((tag = "") => {
    setInterest(tag);
    setWaitlistOpen(true);
  }, []);

  return (
    <>
      <SiteHeader
        signedIn={signedIn}
        onOpenWaitlist={() => openWaitlist()}
      />
      {children}
      <footer className="lp-footer">
        <Link className="lp-logo lp-logo-sm" href="/" aria-label="Z home">
          <span className="lp-logo-mark" aria-hidden="true">
            Z
          </span>
          <span className="lp-logo-word">Z</span>
        </Link>
        <p>Early testing · Model keys stay yours · Workspace sharing coming soon</p>
      </footer>
      <WaitlistModal
        open={waitlistOpen}
        interestTag={interest}
        onClose={() => setWaitlistOpen(false)}
      />
      {/* Expose opener for pricing CTA via custom event */}
      <WaitlistOpenBridge onOpen={openWaitlist} />
    </>
  );
}

function WaitlistOpenBridge({ onOpen }: { onOpen: (tag?: string) => void }) {
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<string>).detail || "";
      onOpen(detail);
    };
    window.addEventListener("z-open-waitlist", handler as EventListener);
    return () =>
      window.removeEventListener("z-open-waitlist", handler as EventListener);
  }, [onOpen]);
  return null;
}

export function openWaitlistEvent(tag = "") {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent("z-open-waitlist", { detail: tag }));
}
