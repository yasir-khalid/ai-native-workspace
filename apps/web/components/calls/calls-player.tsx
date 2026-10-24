"use client";

import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode,
} from "react";
import { toast } from "sonner";
import { audioSrc, type CallSummary, type Chapter } from "@/lib/calls";

const RATE_KEY = "podos.calls.rate";
const VOLUME_KEY = "podos.calls.volume";

type CallsPlayerValue = {
  current: CallSummary | null;
  playing: boolean;
  time: number;
  duration: number;
  rate: number;
  volume: number;
  muted: boolean;
  buffered: number;
  chapterTitle: string | null;
  setCallChapters: (chapters: Chapter[]) => void;
  playCall: (call: CallSummary, startAt?: number) => void;
  toggle: () => void;
  seek: (seconds: number) => void;
  skip: (delta: number) => void;
  setRate: (rate: number) => void;
  setVolume: (volume: number) => void;
  close: () => void;
};

const CallsPlayerContext = createContext<CallsPlayerValue | null>(null);

export function useCallPlayer(): CallsPlayerValue {
  const value = useContext(CallsPlayerContext);
  if (!value) throw new Error("useCallPlayer outside CallsPlayerProvider");
  return value;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

export function CallsPlayerProvider({ children }: { children: ReactNode }) {
  const [current, setCurrent] = useState<CallSummary | null>(null);
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [rate, setRateState] = useState(1);
  const [volume, setVolumeState] = useState(1);
  const [muted, setMutedState] = useState(false);
  const [buffered, setBuffered] = useState(0);
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const currentRef = useRef<CallSummary | null>(null);
  const timeRef = useRef(0);
  const pendingSeekRef = useRef(0);
  const rateRef = useRef(1);
  const volumeRef = useRef(1);
  const mutedRef = useRef(false);
  const pendingPlayRef = useRef<{ call: CallSummary; startAt: number } | null>(null);
  const errorNotifiedRef = useRef(false);
  const lastTimeUpdateRef = useRef(0);

  const updateCurrent = useCallback((call: CallSummary | null) => {
    currentRef.current = call;
    setCurrent(call);
  }, []);

  const setTimeValue = useCallback((value: number) => {
    timeRef.current = value;
    setTime(value);
  }, []);

  const setRate = useCallback((next: number) => {
    const value = Number.isFinite(next) ? clamp(next, 0.5, 3) : 1;
    rateRef.current = value;
    setRateState(value);
    if (audioRef.current) audioRef.current.playbackRate = value;
    try {
      localStorage.setItem(RATE_KEY, String(value));
    } catch {}
  }, []);

  const setVolume = useCallback((next: number) => {
    const value = Number.isFinite(next) ? clamp(next, 0, 1) : 1;
    const nextMuted = value === 0;
    volumeRef.current = value;
    mutedRef.current = nextMuted;
    setVolumeState(value);
    setMutedState(nextMuted);
    if (audioRef.current) {
      audioRef.current.volume = value;
      audioRef.current.muted = nextMuted;
    }
    try {
      localStorage.setItem(VOLUME_KEY, String(value));
    } catch {}
  }, []);

  const seek = useCallback((seconds: number) => {
    const audio = audioRef.current;
    const known = audio && Number.isFinite(audio.duration) ? audio.duration : currentRef.current?.duration ?? 0;
    const value = clamp(seconds, 0, Math.max(0, known));
    setTimeValue(value);
    if (audio) {
      try {
        audio.currentTime = value;
      } catch {}
    }
  }, [setTimeValue]);

  const skip = useCallback((delta: number) => {
    seek(timeRef.current + delta);
  }, [seek]);

  const playCall = useCallback((call: CallSummary, startAt = 0) => {
    const audio = audioRef.current;
    const safeStart = Math.max(0, startAt);
    if (!audio) {
      updateCurrent(call);
      pendingPlayRef.current = { call, startAt: safeStart };
      return;
    }
    const same = currentRef.current?.call_id === call.call_id && Boolean(audio.getAttribute("src"));
    errorNotifiedRef.current = false;
    if (same) {
      updateCurrent(call);
      seek(safeStart);
      if (audio.paused) {
        audio.play().catch(() => toast.error("Couldn't play this call."));
      }
      return;
    }
    updateCurrent(call);
    setChapters([]);
    setBuffered(0);
    setTimeValue(safeStart);
    setDuration(call.duration);
    pendingSeekRef.current = safeStart;
    audio.src = audioSrc(call.audio_url);
    audio.playbackRate = rateRef.current;
    audio.volume = volumeRef.current;
    audio.muted = mutedRef.current;
    audio.load();
    audio.play().catch(() => toast.error("Couldn't play this call."));
  }, [seek, setTimeValue, updateCurrent]);

  const toggle = useCallback(() => {
    const audio = audioRef.current;
    if (!audio || !currentRef.current) return;
    if (audio.paused) {
      if (audio.ended) seek(0);
      audio.play().catch(() => toast.error("Couldn't play this call."));
    } else {
      audio.pause();
    }
  }, [seek]);

  const close = useCallback(() => {
    const audio = audioRef.current;
    if (audio) {
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
    }
    pendingPlayRef.current = null;
    setChapters([]);
    updateCurrent(null);
    setPlaying(false);
    setTimeValue(0);
    setDuration(0);
    setBuffered(0);
  }, [setTimeValue, updateCurrent]);

  const setCallChapters = useCallback((next: Chapter[]) => {
    setChapters(next);
  }, []);

  useEffect(() => {
    const audio = new Audio();
    audio.preload = "metadata";
    audioRef.current = audio;

    let storedRate = 1;
    let storedVolume = 1;
    try {
      const savedRate = Number(localStorage.getItem(RATE_KEY));
      const savedVolume = Number(localStorage.getItem(VOLUME_KEY));
      if (Number.isFinite(savedRate) && savedRate > 0) storedRate = clamp(savedRate, 0.5, 3);
      if (Number.isFinite(savedVolume) && localStorage.getItem(VOLUME_KEY) !== null) storedVolume = clamp(savedVolume, 0, 1);
    } catch {}
    audio.playbackRate = storedRate;
    audio.volume = storedVolume;
    audio.muted = storedVolume === 0;
    rateRef.current = storedRate;
    volumeRef.current = storedVolume;
    mutedRef.current = storedVolume === 0;
    queueMicrotask(() => {
      setRateState(storedRate);
      setVolumeState(storedVolume);
      setMutedState(storedVolume === 0);
    });

    const onTimeUpdate = () => {
      const now = performance.now();
      if (audio.paused || now - lastTimeUpdateRef.current >= 250) {
        lastTimeUpdateRef.current = now;
        setTimeValue(audio.currentTime);
      }
    };
    const onPlay = () => setPlaying(true);
    const onPause = () => {
      setPlaying(false);
      setTimeValue(audio.currentTime);
    };
    const onEnded = () => {
      setPlaying(false);
      setTimeValue(audio.currentTime);
    };
    const onDuration = () => {
      if (Number.isFinite(audio.duration)) setDuration(audio.duration);
    };
    const onLoaded = () => {
      if (Number.isFinite(audio.duration)) setDuration(audio.duration);
      if (pendingSeekRef.current > 0) {
        try {
          audio.currentTime = pendingSeekRef.current;
        } catch {}
      }
      pendingSeekRef.current = 0;
    };
    const onProgress = () => {
      if (!audio.buffered.length) return;
      setBuffered(audio.buffered.end(audio.buffered.length - 1));
    };
    const onVolume = () => {
      volumeRef.current = audio.volume;
      mutedRef.current = audio.muted;
      setVolumeState(audio.volume);
      setMutedState(audio.muted);
    };
    const onError = () => {
      if (!currentRef.current || errorNotifiedRef.current) return;
      errorNotifiedRef.current = true;
      setPlaying(false);
      toast.error("This call's audio isn't available right now.");
    };

    audio.addEventListener("timeupdate", onTimeUpdate);
    audio.addEventListener("play", onPlay);
    audio.addEventListener("pause", onPause);
    audio.addEventListener("ended", onEnded);
    audio.addEventListener("durationchange", onDuration);
    audio.addEventListener("loadedmetadata", onLoaded);
    audio.addEventListener("progress", onProgress);
    audio.addEventListener("volumechange", onVolume);
    audio.addEventListener("error", onError);

    if (pendingPlayRef.current) {
      const pending = pendingPlayRef.current;
      pendingPlayRef.current = null;
      playCall(pending.call, pending.startAt);
    }

    return () => {
      audio.removeEventListener("timeupdate", onTimeUpdate);
      audio.removeEventListener("play", onPlay);
      audio.removeEventListener("pause", onPause);
      audio.removeEventListener("ended", onEnded);
      audio.removeEventListener("durationchange", onDuration);
      audio.removeEventListener("loadedmetadata", onLoaded);
      audio.removeEventListener("progress", onProgress);
      audio.removeEventListener("volumechange", onVolume);
      audio.removeEventListener("error", onError);
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
      audioRef.current = null;
    };
  }, [playCall, setTimeValue]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (!currentRef.current || event.defaultPrevented) return;
      if (target?.closest("input, textarea, select, button, a, [contenteditable='true'], [role='textbox'], [role='slider']")) return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (event.key === " " || event.key === "Spacebar") {
        event.preventDefault();
        toggle();
      } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        skip(-5);
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        skip(5);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [skip, toggle]);

  const chapterTitle = useMemo(() => {
    if (!chapters.length) return null;
    const active = chapters.find((chapter) => time >= chapter.start && time < chapter.end);
    if (active) return active.title;
    return time >= chapters[chapters.length - 1].start ? chapters[chapters.length - 1].title : null;
  }, [chapters, time]);

  const value = useMemo<CallsPlayerValue>(() => ({
    current,
    playing,
    time,
    duration,
    rate,
    volume,
    muted,
    buffered,
    chapterTitle,
    setCallChapters,
    playCall,
    toggle,
    seek,
    skip,
    setRate,
    setVolume,
    close,
  }), [
    buffered, chapterTitle, close, current, duration, muted, playCall, playing, rate, seek, setCallChapters,
    setRate, setVolume, skip, time, toggle, volume,
  ]);

  return <CallsPlayerContext.Provider value={value}>{children}</CallsPlayerContext.Provider>;
}
