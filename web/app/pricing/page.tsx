import type { Metadata } from "next";
import { PricingPage } from "@/components/PricingPage";

export const metadata: Metadata = {
  title: "Pricing — Z",
  description:
    "The core Z agent is free. Bring your own key forever, or join the waitlist for Z's automatic model router.",
};

export default function Page() {
  return <PricingPage />;
}
