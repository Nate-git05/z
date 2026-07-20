import type { Metadata } from "next";
import { LoginPage } from "@/components/LoginPage";

export const metadata: Metadata = {
  title: "Sign in — Z",
  description: "Sign in or create your Z account with email OTP.",
};

export default function Page() {
  return <LoginPage />;
}
