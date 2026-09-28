import { Fragment, useState, useEffect, useRef, useCallback } from "react";
import {
  Check, X, Crosshair, AlertCircle, Radio, Camera, Search,
  AlertTriangle, BadgeCheck, Loader2, Plus, Eye, ChevronRight,
  WifiOff, Wifi, Activity, Clock, MapPin, Map, Zap, HelpCircle,
  Play, Pause
} from "lucide-react";
import { api, asset } from "../lib/api.js";
import { Card, CardHeader, CardBody } from "../components/ui/Card.jsx";
import Button from "../components/ui/Button.jsx";
import Badge from "../components/ui/Badge.jsx";
import Field, { Input, Select } from "../components/ui/Field.jsx";

const TIER_TONES = {
  exact_plate: "ok",
  strong_candidate: "warn",
  attribute_candidate: "neutral",
};

const CATEGORY_LABELS = {
  stolen: "Stolen Vehicle",
  suspect: "Suspect Vehicle",
  blacklisted: "Blacklisted",
  investigation: "Under Investigation",
};

export default function Investigation() {
  const [targets, setTargets] = useState([]);
  const [selectedTargetId, setSelectedTargetId] = useState(null);
  const [targetToDelete, setTargetToDelete] = useState(null);
  const [showSuperseded, setShowSuperseded] = useState(false);
  const [lightboxImg, setLightboxImg] = useState(null);
  
  // Workstation State
  const [timelineData, setTimelineData] = useState(null);
  const [routeData, setRouteData] = useState(null);
  const [predictions, setPredictions] = useState(null);
  const [candidates, setCandidates] = useState([]);
  
  const [selectedItem, setSelectedItem] = useState(null); 
  const [reviewNote, setReviewNote] = useState("");
  
  // Form State
  const [creating, setCreating] = useState(false);
  const [plate, setPlate] = useState("");
  const [vType, setVType] = useState("");
  const [vColor, setVColor] = useState("");
  const [typeRequired, setTypeRequired] = useState(false);
  const [colorRequired, setColorRequired] = useState(false);
  const [category, setCategory] = useState("investigation");
  const [description, setDescription] = useState("");

  const pollRef = useRef(null);

  const loadTargets = useCallback(async () => {
    try {
      const data = await api("/investigations/targets");
      setTargets(data || []);
    } catch (err) {
      console.error(err);
    }
  }, []);

  useEffect(() => {
    loadTargets();
  }, [loadTargets]);

  const loadWorkstationData = useCallback(async (tid) => {
    try {
      const [tl, hist, preds, cands] = await Promise.all([
        api(`/investigations/${tid}/timeline`),
        api(`/investigations/${tid}/history`),
        api(`/investigations/${tid}/predictions`),
        api(`/investigations/targets/${tid}/candidates`)
      ]);
      setTimelineData(tl);
      setRouteData(hist);
      setPredictions(preds);
      setCandidates(cands || []);
    } catch (err) {
      console.error(err);
    }
  }, []);

  useEffect(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    if (!selectedTargetId) {
      setTimelineData(null);
      setRouteData(null);
      setPredictions(null);
      setCandidates([]);
      setSelectedItem(null);
      return;
    }
    
    loadWorkstationData(selectedTargetId);
    pollRef.current = setInterval(() => loadWorkstationData(selectedTargetId), 5000);
    
    return () => clearInterval(pollRef.current);
  }, [selectedTargetId, loadWorkstationData]);

  const loadCandidateDetails = async (candidateId) => {
    try {
      const data = await api(`/investigations/candidates/${candidateId}`);
      setSelectedItem({ type: 'candidate', data });
      setReviewNote("");
    } catch (err) {
      console.error(err);
    }
  };

  const loadObservationEvidence = async (event) => {
    setSelectedItem({ type: "observation", data: { ...event, loading: true } });
    try {
      const evidence = await api(`/investigations/observations/${event.observation_id}/evidence`);
      setSelectedItem({ type: "observation", data: { ...event, ...evidence, loading: false } });
    } catch (err) {
      console.error(err);
      setSelectedItem({ type: "observation", data: { ...event, loading: false, evidenceError: err.message } });
    }
  };

  const verifyCandidate = async (actionStr) => {
    if (!selectedItem || selectedItem.type !== 'candidate') return;
    try {
      await api(`/investigations/candidates/${selectedItem.data.id}/verify`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: actionStr, verifier: "operator_1", review_note: reviewNote }),
      });
      loadWorkstationData(selectedTargetId);
      loadCandidateDetails(selectedItem.data.id);
    } catch (err) {
      console.error(err);
      alert("Failed to verify: " + err.message);
    }
  };

  const toggleTargetStatus = async (target, e) => {
    e.stopPropagation();
    const newStatus = target.status === 'active' ? 'paused' : 'active';
    try {
      await api(`/investigations/targets/${target.id}/status`, {
        method: 'PATCH',
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: newStatus })
      });
      loadTargets();
    } catch (err) {
      console.error(err);
      alert("Failed to update status: " + err.message);
    }
  };

  async function createTarget(e) {
    e.preventDefault();
    if (!plate && !vType && !vColor) {
      alert("Provide at least one of: plate number, vehicle type, or color");
      return;
    }
    setCreating(true);
    try {
      const target = await api("/investigations/targets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          plate_number: plate || null,
          vehicle_type: vType || null,
          vehicle_color: vColor || null,
          type_required: typeRequired && !!vType,
          color_required: colorRequired && !!vColor,
          category,
          description: description || null,
        }),
      });
      await loadTargets();
      setSelectedTargetId(target.id);
      setPlate("");
      setVType("");
      setVColor("");
      setTypeRequired(false);
      setColorRequired(false);
      setDescription("");
    } catch (err) {
      console.error("Failed to create target", err);
      alert("Failed to create target: " + err.message);
    } finally {
      setCreating(false);
    }
  }

  const deleteTarget = async (targetId, deleteData) => {
    try {
      await api(`/investigations/targets/${targetId}?delete_data=${deleteData}`, { method: "DELETE" });
      setTargetToDelete(null);
      if (selectedTargetId === targetId) {
        setSelectedTargetId(null);
      }
      loadTargets();
    } catch (err) {
      console.error(err);
      alert("Failed to delete target: " + err.message);
    }
  };

  const selectedTarget = targets.find(t => t.id === selectedTargetId);

  return (
    <div className="flex flex-col h-[calc(100vh-theme(spacing.16))] bg-background">
      <div className="flex-1 grid grid-cols-1 lg:grid-cols-12 gap-4 p-4 min-h-0">
        
        {/* PANEL 1: TARGETS */}
        <div className="lg:col-span-3 flex flex-col gap-4 min-h-0">
          <Card className="flex-1 flex flex-col min-h-0">
            <CardHeader title="Active Targets" description="Vehicles under investigation" />
            <div className="flex-1 overflow-y-auto divide-y divide-line p-0">
              {targets.map(t => (
                <div
                  key={t.id}
                  className={`p-4 cursor-pointer transition-colors relative ${selectedTargetId === t.id ? 'bg-brand-soft/30 border-l-2 border-brand' : 'hover:bg-surface-2 border-l-2 border-transparent'}`}
                  onClick={() => setSelectedTargetId(t.id)}
                >
                  <div className="flex justify-between items-start">
                    <div>
                      <div className="font-mono font-bold text-[15px]">{t.plate_number || "Unknown Plate"}</div>
                      <div className="text-xs text-ink-3 mt-1">{CATEGORY_LABELS[t.category]}</div>
                    </div>
                    <div className="flex gap-1">
                      <button 
                        onClick={(e) => toggleTargetStatus(t, e)}
                        className={`p-1 rounded transition-colors ${t.status === 'active' ? 'hover:bg-warn-soft/20 text-warn hover:text-warn' : 'hover:bg-ok-soft/20 text-ok hover:text-ok'}`}
                        title={t.status === 'active' ? "Pause Investigation" : "Resume Investigation"}
                      >
                        {t.status === 'active' ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4" />}
                      </button>
                      <button 
                        onClick={(e) => { e.stopPropagation(); setTargetToDelete(t); }}
                        className="p-1 rounded hover:bg-danger-soft/20 text-ink-3 hover:text-danger transition-colors"
                        title="Discard Investigation"
                      >
                        <X className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                  {t.status === 'paused' && (
                    <div className="mt-2 text-xs text-warn font-semibold">Paused</div>
                  )}
                </div>
              ))}
            </div>
            
            <div className="p-4 border-t border-line bg-surface-2/30">
              <h4 className="text-xs font-semibold text-ink-2 mb-3">Create Target</h4>
              <form onSubmit={createTarget} className="flex flex-col gap-3">
                <Field label="Plate">
                  {(a) => <Input {...a} placeholder="e.g. ABC1234" value={plate} onChange={(e) => setPlate(e.target.value.toUpperCase())} />}
                </Field>
                <Field label="Vehicle type">
                  {(a) => (
                    <div className="flex flex-col gap-2">
                      <Select {...a} value={vType} onChange={(e) => setVType(e.target.value)}>
                        <option value="">Unknown / not specified</option>
                        <option value="car">Car</option>
                        <option value="motorcycle">Motorcycle</option>
                        <option value="bus">Bus</option>
                        <option value="truck">Truck</option>
                      </Select>
                      <label className="flex items-center gap-2 text-xs text-ink-3 hover:text-ink-2 cursor-pointer">
                        <input type="checkbox" checked={typeRequired} onChange={(e) => setTypeRequired(e.target.checked)} disabled={!vType} className="rounded border-line bg-surface-2" />
                        Require exact type match
                      </label>
                    </div>
                  )}
                </Field>
                <Field label="Vehicle colour">
                  {(a) => (
                    <div className="flex flex-col gap-2">
                      <Input {...a} placeholder="e.g. red" value={vColor} onChange={(e) => setVColor(e.target.value.toLowerCase())} />
                      <label className="flex items-center gap-2 text-xs text-ink-3 hover:text-ink-2 cursor-pointer">
                        <input type="checkbox" checked={colorRequired} onChange={(e) => setColorRequired(e.target.checked)} disabled={!vColor} className="rounded border-line bg-surface-2" />
                        Require exact color match
                      </label>
                    </div>
                  )}
                </Field>
                <Button variant="primary" type="submit" disabled={creating} className="w-full">
                  <Plus className="h-4 w-4" /> Add Target
                </Button>
              </form>
            </div>
          </Card>
        </div>

        {/* PANEL 2: WORKSTATION MAIN */}
        <div className="lg:col-span-5 flex flex-col gap-4 min-h-0 overflow-y-auto pr-2 pb-4">
          {!selectedTargetId ? (
            <div className="h-full flex items-center justify-center text-ink-3 text-sm flex-col gap-3">
               <Crosshair className="w-8 h-8 opacity-50" />
               Select a target to launch investigator workstation
            </div>
          ) : (
            <>
              {/* LAST OBSERVED HEADER */}
              {timelineData?.last_observed ? (
                <div className="bg-warn-soft/20 border border-warn/30 rounded-xl p-4 flex flex-col gap-3 relative overflow-hidden">
                  <div className="absolute top-0 left-0 w-1 h-full bg-warn" />
                  <div className="flex justify-between items-center pl-2">
                    <div className="flex items-center gap-2 text-warn font-bold tracking-widest text-xs uppercase">
                      <AlertTriangle className="w-4 h-4" />
                      LAST OBSERVED — NOT CURRENT LOCATION
                    </div>
                    <Badge tone="warn" className="font-mono">{timelineData.last_observed.age_seconds < 60 ? "Just now" : Math.floor(timelineData.last_observed.age_seconds / 60) + "m ago"}</Badge>
                  </div>
                  <div className="flex gap-6 pl-2 mt-1">
                    <div>
                      <div className="text-2xs text-ink-3 uppercase tracking-wider mb-0.5">Camera</div>
                      <div className="font-mono font-medium">{timelineData.last_observed.camera_id}</div>
                    </div>
                    <div>
                      <div className="text-2xs text-ink-3 uppercase tracking-wider mb-0.5">Timestamp ({timelineData.last_observed.timestamp_source})</div>
                      <div className="font-mono font-medium">{new Date(timelineData.last_observed.observed_at).toLocaleString()}</div>
                    </div>
                  </div>
                </div>
              ) : (
                <div className="bg-surface-2 border border-line rounded-xl p-4 flex flex-col gap-2">
                   <div className="flex items-center gap-2 text-ink-3 font-bold tracking-widest text-xs uppercase">
                      <Search className="w-4 h-4" /> Scanning Network...
                   </div>
                   <div className="text-sm text-ink-3">No verified observations for this target yet.</div>
                </div>
              )}

              {/* ROUTE VISUALIZATION */}
              {routeData?.route_chains?.length > 0 && (
                <Card>
                  <CardHeader 
                    title="Historical Route Reconstruction" 
                    icon={Map} 
                    actions={
                        <Button variant="ghost" size="sm" onClick={() => setShowSuperseded(!showSuperseded)}>
                           {showSuperseded ? "Hide" : "Show"} Superseded/Invalid
                        </Button>
                    }
                  />
                  <CardBody className="p-4 space-y-4">
                    {routeData.route_chains.filter(c => showSuperseded || ['active', 'extended'].includes(c.status)).map((chain, cIdx) => (
                      <div key={cIdx} className={`bg-surface border-2 rounded-lg p-4 transition-opacity ${['superseded', 'invalidated'].includes(chain.status) ? 'opacity-50 grayscale' : 'opacity-100'} ${chain.mode === 'simulated' ? 'border-warn/40 border-dashed bg-warn-soft/5' : 'border-line'}`}>
                        <div className="flex justify-between items-center mb-3">
                          <span className="text-xs font-semibold text-ink uppercase tracking-wider">Route Chain {cIdx + 1}</span>
                          <div className="flex gap-2">
                            <Badge tone={chain.mode === 'simulated' ? 'warn' : 'brand'}>Mode: {chain.mode}</Badge>
                            <Badge tone={chain.status === 'active' ? 'ok' : 'neutral'}>{chain.status}</Badge>
                          </div>
                        </div>
                        <div className="flex items-center gap-2 overflow-x-auto pb-2">
                          {chain.observations.map((obs, idx) => {
                             const isVerified = obs.verified_action === 'accepted';
                             const isCandidate = obs.machine_assessment && !isVerified;
                             return (
                            <Fragment key={idx}>
                              <div className={`flex flex-col items-center bg-surface border-2 rounded p-2 min-w-[120px] ${isVerified ? 'border-ok/50 bg-ok/5' : isCandidate ? 'border-brand/50 border-dashed bg-brand-soft/10' : 'border-line'}`}>
                                <Camera className={`w-4 h-4 mb-1 ${isVerified ? 'text-ok' : isCandidate ? 'text-brand' : 'text-ink-3'}`} />
                                <span className="font-mono text-xs font-bold">{obs.camera_id}</span>
                                <span className="text-2xs text-ink-3">{new Date(obs.observed_at).toLocaleTimeString()}</span>
                                <span className={`text-[9px] font-bold mt-1 uppercase ${isVerified ? 'text-ok' : isCandidate ? 'text-brand' : 'text-ink-3'}`}>
                                    {isVerified ? 'Verified' : isCandidate ? 'Candidate' : 'Observed'}
                                </span>
                              </div>
                              {idx < chain.observations.length - 1 && (
                                <div className="flex flex-col items-center flex-1 min-w-[60px]">
                                  <div className={`h-1 w-full relative ${chain.mode === 'simulated' ? 'bg-warn/30 border-dashed' : 'bg-line'}`}>
                                    <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 bg-surface rounded-full p-0.5">
                                       <ChevronRight className={`w-3 h-3 ${chain.mode === 'simulated' ? 'text-warn' : 'text-ink-3'}`} />
                                    </div>
                                  </div>
                                </div>
                              )}
                            </Fragment>
                          )})}
                        </div>
                      </div>
                    ))}
                  </CardBody>
                </Card>
              )}

              {/* TIMELINE */}
              <Card>
                <CardHeader title="Chronological Timeline" icon={Clock} />
                <CardBody className="p-5">
                  <div className="relative border-l-2 border-line ml-3 space-y-7">
                    {timelineData?.timeline?.map((evt, idx) => (
                      <div key={idx} className="relative pl-6">
                        {/* Dot */}
                        <div className={`absolute -left-[5px] top-1 w-2.5 h-2.5 rounded-full ring-4 ring-background ${
                          evt.type === 'TARGET_CREATED' ? 'bg-brand' :
                          evt.type === 'OBSERVATION' ? 'bg-ok' :
                          evt.type === 'ROUTE_SEGMENT' ? 'bg-warn' : 'bg-ink-3'
                        }`} />
                        
                        <div className="text-2xs font-mono text-ink-3 mb-1">
                          {evt.timestamp ? new Date(evt.timestamp).toLocaleString() : "Unknown Time"}
                        </div>
                        
                        {evt.type === 'TARGET_CREATED' && (
                          <div className="font-semibold text-sm">Investigation Started</div>
                        )}
                        
                        {evt.type === 'OBSERVATION' && (
                          <div 
                            className="p-3 bg-surface border border-line rounded-lg mt-1 cursor-pointer hover:border-ok/50 transition-colors"
                            onClick={() => loadObservationEvidence(evt)}
                          >
                            <div className="font-bold text-ok flex items-center gap-2 text-sm">
                              <Camera className="w-4 h-4" /> Observation at {evt.camera_id}
                            </div>
                            <div className="text-xs text-ink-3 mt-1 font-mono flex items-center justify-between">
                               <span>ID: {evt.observation_id}</span>
                               <div className="flex gap-2">
                                 <span className="bg-surface-2 px-2 py-0.5 rounded text-2xs uppercase">TS: {evt.timestamp_source || 'UNKNOWN'}</span>
                                 <span className="bg-surface-2 px-2 py-0.5 rounded text-2xs uppercase">MODE: {evt.mode || 'UNKNOWN'}</span>
                               </div>
                            </div>
                            {evt.human_review_state && (
                              <div className="mt-2 text-2xs uppercase tracking-wider font-semibold">
                                {evt.human_review_state === 'accepted' ? <span className="text-ok">✓ Verified</span> : 
                                 evt.human_review_state === 'rejected' ? <span className="text-danger">✗ Rejected</span> : 
                                 <span className="text-warn">● Pending Review</span>}
                              </div>
                            )}
                          </div>
                        )}
                        
                        {evt.type === 'ROUTE_SEGMENT' && (
                          <div className="p-3 border border-dashed border-warn/30 bg-warn-soft/10 rounded-lg mt-1">
                            {evt.state === 'UNOBSERVED_GAP' ? (
                              <div className="flex items-center gap-2 text-warn font-medium text-sm"><HelpCircle className="w-4 h-4"/> Unobserved Gap / Uncertain Segment</div>
                            ) : (
                              <div className="flex items-center gap-2 text-ink font-medium text-sm">
                                <MapPin className="w-4 h-4 text-brand"/> 
                                <span>{evt.machine_assessment ? evt.machine_assessment.replace(/_/g, ' ') : "Machine Candidate Link"}</span>
                                <Badge tone="neutral" className="ml-auto">Score: {Number(evt.score || 0).toFixed(2)}</Badge>
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </CardBody>
              </Card>

              {/* PREDICTIONS */}
              {predictions?.predictions?.length > 0 && (
                <Card className="border-brand/30 shadow-[0_0_15px_rgba(var(--c-brand),0.1)]">
                  <CardHeader title="AI Predictions" icon={Zap} />
                  <div className="p-3 bg-brand/10 border-b border-brand/20 text-xs text-brand font-bold flex items-center gap-2 tracking-wide">
                    <AlertCircle className="w-4 h-4"/> HYPOTHESIS — NOT A CONFIRMED OBSERVATION
                  </div>
                  <CardBody className="p-4 space-y-3">
                    {predictions.predictions.map((p, i) => (
                      <div key={i} className="flex flex-col p-3 border border-brand/20 rounded-lg bg-surface relative overflow-hidden">
                        <div className="absolute left-0 top-0 bottom-0 w-1 bg-brand" />
                        <div className="flex justify-between items-start pl-3 mb-2">
                          <div>
                            <div className="text-2xs text-ink-3 uppercase tracking-wider mb-1">Predicted Candidate Camera</div>
                            <div className="font-mono font-bold text-lg">{p.candidate_camera_id || p.predicted_camera_id}</div>
                          </div>
                          <div className="flex flex-col items-end gap-1">
                             <Badge tone={p.mode === 'simulated' ? 'warn' : 'brand'}>{p.mode}</Badge>
                             <span className="text-2xs text-ink-3">Graph v{p.graph_version || 1} · Model {p.model_version || '2.1'}</span>
                          </div>
                        </div>
                        <div className="pl-3 grid grid-cols-2 gap-4 mt-2">
                           <div>
                              <div className="text-2xs text-ink-3 uppercase mb-1">Time Window (IQR)</div>
                              <div className="font-mono text-xs font-medium text-ink-2">
                                  {p.estimated_time_window ? `Start: ${new Date(p.estimated_time_window.start).toLocaleTimeString()}` : "Insufficient Data"}
                              </div>
                              <div className="font-mono text-xs text-ink-3">
                                  {p.estimated_time_window ? `End: ${new Date(p.estimated_time_window.end).toLocaleTimeString()}` : ""}
                              </div>
                           </div>
                           <div>
                              <div className="text-2xs text-ink-3 uppercase mb-1">Support & Dispersion</div>
                              <div className="text-xs text-ink-2">Unique Sessions: <span className="font-mono font-medium">{p.unique_session_count || 0}</span></div>
                              {p.dispersion && <div className="text-xs text-ink-2">Dispersion (P25-P75): <span className="font-mono">{Math.round(p.dispersion.p25_seconds || 0)}s - {Math.round(p.dispersion.p75_seconds || 0)}s</span></div>}
                           </div>
                        </div>
                      </div>
                    ))}
                  </CardBody>
                </Card>
              )}

              {/* PENDING CANDIDATES */}
              {candidates.length > 0 && (
                <Card>
                  <CardHeader title="Unreviewed Candidates" icon={Crosshair} />
                  <div className="divide-y divide-line">
                    {candidates.filter(c => c.status === 'completed').map(c => (
                      <div 
                        key={c.id} 
                        className="p-3 cursor-pointer hover:bg-surface-2 flex justify-between items-center"
                        onClick={() => loadCandidateDetails(c.id)}
                      >
                        <div className="flex items-center gap-3">
                          <div className="w-10 h-10 rounded-lg bg-warn-soft/30 border border-warn/20 flex items-center justify-center">
                             <AlertCircle className="w-4 h-4 text-warn" />
                          </div>
                          <div>
                            <div className="font-mono font-bold">{c.ocr_consensus?.best_plate || "No plate"}</div>
                            <div className="text-xs text-ink-3">{c.camera_id} · Track #{c.track_id}</div>
                          </div>
                        </div>
                        <Badge tone={c.machine_assessment === 'strong_candidate' ? 'ok' : c.machine_assessment === 'temporal_impossible' ? 'danger' : 'warn'}>
                          {c.machine_assessment ? c.machine_assessment.replace(/_/g, ' ') : "Review Needed"}
                        </Badge>
                      </div>
                    ))}
                  </div>
                </Card>
              )}
            </>
          )}
        </div>

        {/* PANEL 3: EVIDENCE VIEWER */}
        <div className="lg:col-span-4 flex flex-col gap-4 min-h-0">
          <Card className="flex-1 flex flex-col min-h-0">
            <CardHeader title="Evidence & Review" icon={Eye} />
            
            {!selectedItem ? (
              <div className="flex-1 flex flex-col items-center justify-center text-ink-3 text-sm gap-2">
                 <Camera className="w-8 h-8 opacity-50" />
                 Select an observation or candidate to review
              </div>
            ) : selectedItem.type === 'observation' ? (
              <div className="flex-1 flex flex-col overflow-y-auto p-4">
                <h3 className="text-xs font-semibold text-ink uppercase tracking-wider mb-1">Historical Observation</h3>
                <p className="text-xs text-ink-3 mb-4 font-mono">{selectedItem.data.observation_id}</p>
                {selectedItem.data.loading ? (
                  <div className="flex flex-1 items-center justify-center gap-2 text-sm text-ink-3">
                    <Loader2 className="h-4 w-4 animate-spin" /> Loading evidence…
                  </div>
                ) : selectedItem.data.evidence_available ? (
                  <>
                    <div className="mb-3 text-xs text-ink-3">
                      Evidence is linked by the observation’s persisted candidate reference.
                    </div>
                    <div className="flex flex-col gap-4">
                      {selectedItem.data.evidence?.map((ev) => (
                        <div key={ev.id} className="border border-line rounded-xl p-3 bg-surface shadow-sm">
                          <div className="flex justify-between text-xs text-ink-2 mb-2">
                            <span className="font-bold font-mono">FRAME {ev.frame_index}</span>
                            <Badge tone={ev.quality_score >= 0.7 ? "ok" : ev.quality_score >= 0.4 ? "warn" : "danger"}>
                              Quality: {Math.round(ev.quality_score * 100)}%
                            </Badge>
                          </div>
                          <div className="grid grid-cols-1 gap-3">
                            {ev.raw_path && <img src={asset(`/${ev.raw_path}`)} alt="Raw evidence frame" className="w-full rounded-lg border border-line cursor-pointer hover:opacity-90" loading="lazy" onClick={() => setLightboxImg(asset(`/${ev.raw_path}`))} />}
                            {ev.enhanced_path && <img src={asset(`/${ev.enhanced_path}`)} alt="Enhanced evidence frame" className="w-full rounded-lg border border-brand/30 cursor-pointer hover:opacity-90" loading="lazy" onClick={() => setLightboxImg(asset(`/${ev.enhanced_path}`))} />}
                          </div>
                          {ev.ocr_candidate && <div className="font-mono text-sm font-bold text-center mt-2">{ev.ocr_candidate}</div>}
                        </div>
                      ))}
                      {selectedItem.data.evidence?.length === 0 && <p className="text-sm text-ink-3">No selected evidence frames were persisted for this observation.</p>}
                    </div>
                  </>
                ) : (
                  <div className="bg-warn-soft/20 text-warn border border-warn/30 p-4 rounded-lg text-sm text-left shadow-sm">
                    <p className="font-bold mb-2">Evidence Limitation</p>
                    {selectedItem.data.evidenceError || "This historical observation has no deterministic candidate-evidence link, so no evidence is shown rather than guessing."}
                  </div>
                )}
              </div>
            ) : (
              // CANDIDATE EVIDENCE VIEWER
              <div className="flex-1 flex flex-col overflow-y-auto">
                 {/* Score Breakdown */}
                 <div className="p-4 border-b border-line bg-surface-2/30">
                  <h3 className="text-xs font-semibold text-ink uppercase tracking-wider mb-3">Candidate Details</h3>
                  <div className="font-mono text-2xl font-bold mb-1 text-ink">
                    {selectedItem.data.ocr_consensus?.best_plate || "—"}
                  </div>
                  <div className="text-sm text-ink-3 mb-4">
                    Track #{selectedItem.data.track_id} at <span className="font-medium text-ink-2">{selectedItem.data.camera_id}</span>
                  </div>
                  
                  <div className="space-y-2.5">
                    {Object.entries(selectedItem.data.score_breakdown || {}).map(([key, val]) => (
                      <div key={key} className="flex items-center gap-2 text-xs">
                        <span className="w-28 text-ink-2 capitalize truncate">{key.replaceAll('_', ' ')}</span>
                        <div className="flex-1 h-1.5 bg-surface-2 rounded-full overflow-hidden">
                          <div
                            className="h-full rounded-full transition-all duration-500"
                            style={{
                              width: `${(val || 0) * 100}%`,
                              backgroundColor: val >= 0.8 ? 'var(--c-ok, #22c55e)' : val >= 0.5 ? 'rgb(var(--c-brand))' : 'rgb(var(--c-warn))',
                            }}
                          />
                        </div>
                        <span className="w-10 text-right font-mono text-ink-3">{Math.round((val || 0) * 100)}%</span>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="p-4 flex-1">
                  <h3 className="text-xs font-semibold text-ink uppercase tracking-wider mb-3">
                    Evidence Frames ({selectedItem.data.evidence?.length || 0})
                  </h3>
                  <div className="flex flex-col gap-4">
                    {selectedItem.data.evidence?.map((ev) => (
                      <div key={ev.id} className="border border-line rounded-xl p-3 bg-surface shadow-sm">
                        <div className="flex justify-between text-xs text-ink-2 mb-2">
                          <span className="font-bold font-mono">FRAME {ev.frame_index}</span>
                          <Badge tone={ev.quality_score >= 0.7 ? "ok" : ev.quality_score >= 0.4 ? "warn" : "danger"}>
                            Quality: {Math.round(ev.quality_score * 100)}%
                          </Badge>
                        </div>
                        <div className="flex flex-wrap gap-2 mb-2">
                          {ev.vehicle_type && <Badge tone="neutral" className="uppercase text-2xs">{ev.vehicle_type}</Badge>}
                          {ev.vehicle_color && <Badge tone="neutral" className="uppercase text-2xs">Color: {ev.vehicle_color}</Badge>}
                          {ev.plate_number && <Badge tone="brand" className="uppercase tracking-widest text-2xs">{ev.plate_number}</Badge>}
                        </div>
                        <div className="flex flex-col xl:flex-row gap-3 mb-2">
                          <div className="flex-1 flex flex-col gap-1">
                            <span className="text-2xs font-semibold text-ink-3 uppercase tracking-wider">Raw Capture</span>
                            <div className="relative aspect-video w-full overflow-hidden rounded-lg border border-line bg-surface-2">
                              {ev.raw_path ? (
                                <img src={asset(`/${ev.raw_path}`)} alt="Raw frame" className="h-full w-full object-cover transition-transform hover:scale-105 cursor-pointer" loading="lazy" onClick={() => setLightboxImg(asset(`/${ev.raw_path}`))} />
                              ) : (
                                <div className="flex h-full w-full items-center justify-center text-xs text-ink-3">No Image</div>
                              )}
                            </div>
                          </div>
                          
                          {ev.enhanced_path && (
                            <div className="flex-1 flex flex-col gap-1">
                              <span className="text-2xs font-semibold text-brand uppercase tracking-wider flex items-center gap-1"><Check className="w-3 h-3"/> Enhanced</span>
                              <div className="relative aspect-video w-full overflow-hidden rounded-lg border border-brand/30 bg-surface-2 shadow-[0_0_10px_rgba(var(--c-brand),0.1)]">
                                <img src={asset(`/${ev.enhanced_path}`)} alt="Enhanced frame" className="h-full w-full object-cover transition-transform hover:scale-105 cursor-pointer" loading="lazy" onClick={() => setLightboxImg(asset(`/${ev.enhanced_path}`))} />
                              </div>
                            </div>
                          )}
                        </div>
                        {ev.ocr_candidate && (
                          <div className="font-mono text-sm font-bold text-center mt-2 bg-surface-2/50 border border-line rounded-lg py-1.5">
                            {ev.ocr_candidate}
                            {ev.ocr_confidence != null && (
                              <span className="ml-2 text-2xs font-normal text-ink-3">
                                ({Math.round(ev.ocr_confidence * 100)}%)
                              </span>
                            )}
                          </div>
                        )}
                        <div className="flex gap-2 text-2xs text-ink-3 mt-2 font-medium">
                          <span className="capitalize">{[ev.vehicle_color, ev.vehicle_type].filter(Boolean).join(" ")}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Review Note + Action Buttons */}
                <div className="p-4 border-t border-line flex flex-col gap-3 mt-auto bg-surface">
                  <Field label="Review Note">
                    {(a) => (
                      <Input
                        {...a}
                        value={reviewNote}
                        onChange={(e) => setReviewNote(e.target.value)}
                        placeholder="Operator notes..."
                      />
                    )}
                  </Field>
                  <div className="flex gap-3">
                    <Button
                      variant="primary"
                      className="flex-1"
                      onClick={() => verifyCandidate("verify")}
                      disabled={selectedItem.data.status !== "completed"}
                    >
                      <Check className="w-4 h-4" /> Verify Match
                    </Button>
                    <Button
                      variant="danger"
                      className="flex-1"
                      onClick={() => verifyCandidate("reject")}
                      disabled={selectedItem.data.status !== "completed"}
                    >
                      <X className="w-4 h-4" /> Reject
                    </Button>
                  </div>
                </div>
              </div>
            )}
          </Card>
        </div>

      </div>

      {/* LIGHTBOX MODAL */}
      {lightboxImg && (
        <div className="fixed inset-0 bg-background/90 backdrop-blur-sm z-[100] flex items-center justify-center p-4" onClick={() => setLightboxImg(null)}>
          <div className="relative max-w-full max-h-full flex items-center justify-center">
            <img src={lightboxImg} alt="Enlarged evidence" className="max-w-[90vw] max-h-[90vh] object-contain rounded-lg shadow-2xl" onClick={(e) => e.stopPropagation()} />
            <button onClick={() => setLightboxImg(null)} className="absolute -top-4 -right-4 md:top-4 md:right-4 p-2 bg-surface border border-line rounded-full text-ink hover:text-danger shadow-lg transition-colors">
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>
      )}

      {/* DISCARD MODAL */}
      {targetToDelete && (
        <div className="fixed inset-0 bg-background/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-surface border border-line rounded-xl shadow-2xl max-w-md w-full overflow-hidden">
            <div className="p-4 border-b border-line flex justify-between items-center bg-danger-soft/10">
              <h3 className="font-bold text-danger flex items-center gap-2">
                <AlertTriangle className="w-5 h-5" /> Discard Investigation
              </h3>
              <button onClick={() => setTargetToDelete(null)} className="text-ink-3 hover:text-ink">
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="p-4 text-sm text-ink-2">
              <p className="mb-4">
                You are about to discard the investigation for <strong className="font-mono text-ink">{targetToDelete.plate_number || "Unknown Plate"}</strong>.
              </p>
              <p className="mb-4 text-xs text-ink-3">
                Please choose how to handle the evidence data that has already been gathered for this subject.
              </p>
              
              <div className="flex flex-col gap-3">
                <Button variant="outline" className="justify-start h-auto p-3" onClick={() => deleteTarget(targetToDelete.id, false)}>
                  <div className="text-left flex flex-col items-start w-full">
                    <div className="font-bold text-ink">Stop Investigation (Keep Data)</div>
                    <div className="text-xs text-ink-3 mt-1 whitespace-normal">Stops tracking, but retains all candidate evidence gathered so far in the database.</div>
                  </div>
                </Button>
                <Button variant="danger" className="justify-start h-auto p-3" onClick={() => deleteTarget(targetToDelete.id, true)}>
                  <div className="text-left flex flex-col items-start w-full">
                    <div className="font-bold text-white">Stop & Delete Data</div>
                    <div className="text-xs text-white/80 mt-1 whitespace-normal">Completely removes the target and permanently deletes all gathered evidence frames and tracks to save space.</div>
                  </div>
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
