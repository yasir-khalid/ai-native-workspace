"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useMe } from "@/components/shell/session";

export default function Home() {
  const { me, pods } = useMe();
  const router = useRouter();
  useEffect(() => {
    if (!me || !pods) return;
    if (pods.length) router.replace(`/${pods[0].pod_id}/today`);
    else router.replace("/login?nopods=1");
  }, [me, pods, router]);
  return null;
}
