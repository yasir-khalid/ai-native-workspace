import { Suspense } from "react";
import PodLayoutClient from "./pod-layout-client";

// Route params are request-time data; the client shell reads them inside a Suspense boundary.
export default function PodLayout({ children }: { children: React.ReactNode }) {
  return (
    <Suspense fallback={null}>
      <PodLayoutClient>{children}</PodLayoutClient>
    </Suspense>
  );
}
