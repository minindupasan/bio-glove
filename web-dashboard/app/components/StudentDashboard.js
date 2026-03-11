"use client";

import { useState, useEffect, useRef } from "react";
import { db } from "@/lib/firebase";
import { ref, onValue } from "firebase/database";

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
    <div className="dashboard-container">
      {/* Header */}
      <div className="dashboard-header">
        <div className="header-left">
          <h2>Smart Classroom</h2>
          <span className="header-badge badge-student">
            Student {studentId.replace("s0", "")}
          </span>
        </div>
        <div className="header-right">
          <button className="logout-btn" onClick={onLogout}>
            ← Logout
          </button>
        </div>
      </div>

      {/* Student Grid */}
      <div className="student-grid">
        {/* Camera View */}
        <div className="glass-card panel" style={{ padding: 0, overflow: "hidden" }}>
          <div className="camera-container">
            <video
              ref={videoRef}
              autoPlay
              playsInline
              muted
              style={{ transform: "scaleX(-1)" }}
            />
            <div className="camera-label">
              <span className="live-dot"></span>
              {cameraActive ? "LIVE" : "CONNECTING..."}
            </div>
          </div>
        </div>

        {/* Sidebar */}
        <div className="student-sidebar">
          {/* Teacher Transcript */}
          <div className="glass-card panel student-transcript">
            <div className="panel-header">
              <div className="panel-icon indigo">🔊</div>
              <span className="panel-title">Teacher Speaking</span>
            </div>
            <div className="transcript-area">
              {transcript ? (
                <p>{transcript.text}</p>
              ) : (
                <span className="shimmer">
                  Waiting for teacher to speak...
                </span>
              )}
            </div>
            {transcript?.timestamp && (
              <div className="sub-value">
                Last updated:{" "}
                {new Date(transcript.timestamp).toLocaleTimeString()}
              </div>
            )}
          </div>

          {/* Sign Detection Status */}
          <div className="glass-card panel sign-status-card">
            <div className="panel-header">
              <div className="panel-icon cyan">🤟</div>
              <span className="panel-title">Your Sign</span>
            </div>
            <div className="sign-icon">
              {signLabel ? "✅" : "🤲"}
            </div>
            <div className="sign-label">
              {signLabel ? signLabel.toUpperCase() : "No sign detected"}
            </div>
            {signLabel && (
              <div className="confidence-bar" style={{ justifyContent: "center", marginTop: 8 }}>
                <span className="confidence-text">
                  Confidence: {(signConf * 100).toFixed(0)}%
                </span>
              </div>
            )}
            <div className="sub-value" style={{ marginTop: 12 }}>
              Your sign language is being sent to the teacher
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
