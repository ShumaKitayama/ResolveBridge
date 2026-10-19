// Mux a video track with an audio file. usage: mux_av <video> <audio> <out.mp4> [video2 start_frame fps] (second video appended from start_frame)
import Foundation
import AVFoundation

let a = CommandLine.arguments
let v = AVURLAsset(url: URL(fileURLWithPath: a[1]))
let au = AVURLAsset(url: URL(fileURLWithPath: a[2]))
let comp = AVMutableComposition()
let vt = comp.addMutableTrack(withMediaType: .video, preferredTrackID: kCMPersistentTrackID_Invalid)!
let src = v.tracks(withMediaType: .video)[0]
if a.count > 6 {
  // first video up to start_frame, then the second video from start_frame to its end
  let fps = Int32(a[6])!, sf = Int64(a[5])!
  let cut = CMTime(value: sf, timescale: fps)
  try vt.insertTimeRange(CMTimeRange(start: .zero, duration: cut), of: src, at: .zero)
  let v2 = AVURLAsset(url: URL(fileURLWithPath: a[4]))
  try vt.insertTimeRange(CMTimeRange(start: cut, end: v2.duration), of: v2.tracks(withMediaType: .video)[0], at: cut)
} else {
  try vt.insertTimeRange(CMTimeRange(start: .zero, duration: v.duration), of: src, at: .zero)
}
let at = comp.addMutableTrack(withMediaType: .audio, preferredTrackID: kCMPersistentTrackID_Invalid)!
try at.insertTimeRange(CMTimeRange(start: .zero, duration: min(au.duration, comp.duration)), of: au.tracks(withMediaType: .audio)[0], at: .zero)
let out = URL(fileURLWithPath: a[3])
try? FileManager.default.removeItem(at: out)
let ex = AVAssetExportSession(asset: comp, presetName: AVAssetExportPresetHighestQuality)!
ex.outputURL = out
ex.outputFileType = .mp4
let sem = DispatchSemaphore(value: 0)
ex.exportAsynchronously { sem.signal() }
sem.wait()
print(ex.status == .completed ? "ok" : "fail \(String(describing: ex.error))")
