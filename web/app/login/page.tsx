import type { Metadata } from "next";
import { LoginPage } from "@/components/LoginPage";

export const metadata: Metadata = {
  title: "Sign in — Z",
  description: "Sign in or create your Z account.",
};

type Props = {
  searchParams: Promise<{ redirect_uri?: string; state?: string }>;
};

export default async function Page({ searchParams }: Props) {
  const sp = await searchParams;
  return (
    <LoginPage
      redirectUri={sp.redirect_uri || ""}
      callbackState={sp.state || ""}
    />
  );
}
