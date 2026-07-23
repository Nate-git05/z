import type { Metadata } from "next";
import { GuidePage } from "@/components/GuidePage";

export const metadata: Metadata = {
  title: "Guide — Z",
  description:
    "Install Z, sign in, choose BYOK or the router, and the everyday commands you'll actually use.",
};

export default function Page() {
  return <GuidePage />;
}
