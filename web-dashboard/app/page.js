"use client";

import { useState } from "react";
import TeacherDashboard from "./components/TeacherDashboard";
import StudentDashboard from "./components/StudentDashboard";
import { Card, CardContent, CardDescription, CardHeader, CardTitle, CardFooter } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { UserRound, GraduationCap, ArrowRight } from "lucide-react";

export default function Home() {
  const [role, setRole] = useState(null); // 'teacher' | 'student'
  const [studentId, setStudentId] = useState("s01");
  const [loggedIn, setLoggedIn] = useState(false);

  if (!loggedIn) {
    return (
      <div className="min-h-screen bg-black flex items-center justify-center p-4">
        <Card className="w-full max-w-md bg-zinc-950 border-zinc-800">
          <CardHeader className="text-center pb-6">
            <CardTitle className="text-3xl font-bold text-white tracking-tight">
              Smart Classroom
            </CardTitle>
            <CardDescription className="text-zinc-400 text-base">
              Bio-Glove Monitoring Dashboard
            </CardDescription>
          </CardHeader>

          <CardContent className="space-y-6">
            <div className="grid grid-cols-2 gap-4">
              <button
                className={`flex flex-col items-center gap-3 p-4 rounded-xl border transition-all ${
                  role === "teacher"
                    ? "border-white bg-white/5 text-white shadow-[0_0_15px_rgba(255,255,255,0.05)]"
                    : "border-zinc-800 bg-zinc-900/50 text-zinc-400 hover:border-zinc-700 hover:text-zinc-300 hover:bg-zinc-900"
                }`}
                onClick={() => setRole("teacher")}
              >
                <div className={`p-3 rounded-full ${role === "teacher" ? "bg-white/10" : "bg-zinc-800/50"}`}>
                  <UserRound className="w-6 h-6" />
                </div>
                <span className="font-semibold">Teacher</span>
              </button>
              
              <button
                className={`flex flex-col items-center gap-3 p-4 rounded-xl border transition-all ${
                  role === "student"
                    ? "border-white bg-white/5 text-white shadow-[0_0_15px_rgba(255,255,255,0.05)]"
                    : "border-zinc-800 bg-zinc-900/50 text-zinc-400 hover:border-zinc-700 hover:text-zinc-300 hover:bg-zinc-900"
                }`}
                onClick={() => setRole("student")}
              >
                <div className={`p-3 rounded-full ${role === "student" ? "bg-white/10" : "bg-zinc-800/50"}`}>
                  <GraduationCap className="w-6 h-6" />
                </div>
                <span className="font-semibold">Student</span>
              </button>
            </div>

            {role === "student" && (
              <div className="pt-2 animate-in fade-in slide-in-from-top-4 duration-300">
                <Select value={studentId} onValueChange={setStudentId}>
                  <SelectTrigger className="w-full bg-zinc-950 border-zinc-800 text-zinc-200 h-11 focus:ring-1 focus:ring-white">
                    <SelectValue placeholder="Select Student" />
                  </SelectTrigger>
                  <SelectContent className="bg-zinc-950 border-zinc-800 text-zinc-200">
                    <SelectItem value="s01">Student 01</SelectItem>
                    <SelectItem value="s02">Student 02</SelectItem>
                    <SelectItem value="s03">Student 03</SelectItem>
                    <SelectItem value="s04">Student 04</SelectItem>
                    <SelectItem value="s05">Student 05</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            )}

            <div className="pt-4">
              <Button
                className={`w-full h-12 text-base font-medium transition-all duration-300 ${
                  role 
                    ? "bg-white text-black hover:bg-zinc-200 shadow-[0_0_20px_rgba(255,255,255,0.15)] hover:shadow-[0_0_25px_rgba(255,255,255,0.25)]" 
                    : "bg-zinc-900 border border-zinc-800 text-zinc-500 hover:bg-zinc-900"
                }`}
                disabled={!role}
                onClick={() => role && setLoggedIn(true)}
              >
                Enter Dashboard
                <ArrowRight className={`w-5 h-5 ml-2 transition-transform duration-300 ${role ? "translate-x-1" : ""}`} />
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  const handleLogout = () => {
    setLoggedIn(false);
    setRole(null);
  };

  if (role === "teacher") {
    return <TeacherDashboard onLogout={handleLogout} />;
  }

  return (
    <StudentDashboard studentId={studentId} onLogout={handleLogout} />
  );
}
