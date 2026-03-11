"use client";

import { useState, useEffect, useRef } from "react";
import { db } from "@/lib/firebase";
import { ref, onValue } from "firebase/database";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { UserRound, Camera, Radio, History, HandMetal, AlertCircle, LogOut } from "lucide-react";

export default function StudentDashboard({ studentId, onLogout }) {
  const videoRef = useRef(null);
  const [cameraActive, setCameraActive] = useState(false);
  const [transcript, setTranscript] = useState(null);
  const [signData, setSignData] = useState(null);

  // Start camera
  useEffect(() => {
    let stream = null;

    async function startCamera() {
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: "user", width: 640, height: 480 },
          audio: false,
        });
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
          setCameraActive(true);
        }
      } catch (err) {
        console.error("Camera access error:", err);
      }
    }

    startCamera();

    return () => {
      if (stream) {
        stream.getTracks().forEach((t) => t.stop());
      }
    };
  }, []);

  // Listen for teacher transcript
  useEffect(() => {
    const tRef = ref(db, `transcription/${studentId}`);
    const unsub = onValue(tRef, (snap) => {
      setTranscript(snap.val());
    });
    return () => unsub();
  }, [studentId]);

  // Auto-clear transcript after 20 seconds
  useEffect(() => {
    if (!transcript) return;

    const timer = setTimeout(() => {
      setTranscript(null);
    }, 20000);

    return () => clearTimeout(timer);
  }, [transcript]);

  // Listen for sign detection updates
  useEffect(() => {
    const sRef = ref(db, `sign/${studentId}`);
    const unsub = onValue(sRef, (snap) => {
      setSignData(snap.val());
    });
    return () => unsub();
  }, [studentId]);

  const signLabel = signData?.label || null;
  const signConf = signData?.confidence ?? 0;

  return (
    <div className="min-h-screen bg-black p-4 md:p-8 text-zinc-200">
      {/* Header */}
      <header className="flex flex-col md:flex-row items-center justify-between gap-4 p-4 md:p-6 mb-8 rounded-xl bg-zinc-950 border border-zinc-800 shadow-sm">
        <div className="flex items-center gap-4">
          <h2 className="text-xl font-bold text-white tracking-tight">
            Smart Classroom
          </h2>
          <Badge variant="outline" className="bg-white/10 text-white border-white/20">
            Student {studentId.replace("s0", "")}
          </Badge>
        </div>
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-zinc-900 border border-zinc-800 text-sm font-medium text-zinc-300">
            <UserRound className="w-4 h-4 text-white" />
            <span className="hidden sm:inline">Active Session</span>
          </div>
          <Button variant="ghost" onClick={onLogout} className="text-zinc-400 hover:text-white hover:bg-white/10">
            <LogOut className="w-4 h-4 mr-2" />
            Logout
          </Button>
        </div>
      </header>

      {/* Main Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        
        {/* Camera Feed (Spans 2 columns on large screens) */}
        <Card className="lg:col-span-2 bg-zinc-950 border-zinc-800 flex flex-col overflow-hidden">
          <CardHeader className="flex flex-row items-center bg-zinc-900/50 py-3 px-4 border-b border-zinc-800">
            <Camera className="w-4 h-4 text-zinc-400 mr-2" />
            <CardTitle className="text-sm font-medium text-zinc-300">Live Camera Feed</CardTitle>
            <div className="ml-auto flex items-center gap-2">
                <Badge variant="outline" className={`px-2 py-0.5 animate-pulse ${cameraActive ? "bg-emerald-500/10 text-emerald-500 border-emerald-500/20" : "bg-red-500/10 text-red-400 border-red-500/20"}`}>
                  <Radio className="w-3 h-3 mr-1" /> {cameraActive ? "Live" : "Connecting..."}
                </Badge>
            </div>
          </CardHeader>
          <CardContent className="p-0 flex-1 relative bg-black min-h-[300px] flex items-center justify-center">
            <video
              ref={videoRef}
              autoPlay
              playsInline
              muted
              className="w-full h-full object-cover"
              style={{ transform: "scaleX(-1)" }}
            />
          </CardContent>
        </Card>

        {/* Sidebar */}
        <div className="flex flex-col gap-6">
          
          {/* Sign Language Status */}
          <Card className="bg-zinc-950 border-zinc-800 text-center flex flex-col items-center justify-center py-6 h-[200px]">
            <CardContent className="flex flex-col items-center justify-center w-full p-0">
              <div className="inline-flex items-center justify-center p-3 rounded-full bg-cyan-500/10 mb-3 text-cyan-400">
                <HandMetal className="w-6 h-6" />
              </div>
              <h3 className="text-zinc-500 text-xs font-medium uppercase tracking-widest mb-2">Current Sign</h3>
              
              {signLabel ? (
                <div className="flex flex-col items-center w-full px-6">
                  <span className="text-3xl font-extrabold text-cyan-400 tracking-tight mb-2">{signLabel.toUpperCase()}</span>
                  <div className="flex items-center gap-3 w-full max-w-[200px]">
                    <Progress value={signConf * 100} className="h-1.5 bg-zinc-800 flex-1" indicatorClassName="bg-cyan-500" />
                    <span className="text-xs text-zinc-500 w-10 text-right">{(signConf * 100).toFixed(0)}%</span>
                  </div>
                </div>
              ) : (
                <div className="text-sm font-medium text-zinc-600 italic py-2 animate-pulse mt-2">
                  Listening for signs...
                </div>
              )}
            </CardContent>
          </Card>

          {/* Teacher's Transcript */}
          <Card className="bg-zinc-950 border-zinc-800 flex-1 flex flex-col h-full">
            <CardHeader className="flex flex-row items-center py-3 bg-zinc-900/30 border-b border-zinc-800">
              <History className="w-4 h-4 text-indigo-400 mr-2" />
              <CardTitle className="text-sm font-medium text-zinc-300 flex-1">Teacher Speaking</CardTitle>
              {transcript?.timestamp && (
                <span className="text-xs text-zinc-500 mr-3">
                  {new Date(transcript.timestamp).toLocaleTimeString()}
                </span>
              )}
              {transcript?.text && (
                <Button variant="ghost" size="sm" onClick={() => setTranscript(null)} className="text-zinc-500 hover:text-red-400 h-6 px-2 text-xs">
                  Clear
                </Button>
              )}
            </CardHeader>
            <CardContent className="flex flex-col flex-1 p-0">
              <div className="flex-1 p-5 overflow-y-auto max-h-[300px] min-h-[160px]">
                {transcript?.text ? (
                  <div className="bg-indigo-500/10 border-l-4 border-indigo-500 p-4 rounded-r-lg text-zinc-200 leading-relaxed shadow-sm animate-in fade-in slide-in-from-left-2 duration-300">
                    <p className="italic text-lg">"{transcript.text}"</p>
                  </div>
                ) : (
                  <div className="h-full flex flex-col items-center justify-center text-zinc-600 opacity-60 mt-8">
                    <Radio className="w-6 h-6 mb-3" />
                    <p className="text-sm text-center">Waiting for teacher's broadcast...</p>
                  </div>
                )}
              </div>
            </CardContent>
          </Card>

        </div>
      </div>
    </div>
  );
}
