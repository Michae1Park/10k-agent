import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "10k-agent",
  description: "Grounded, cited research over SEC 10-K filings",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
