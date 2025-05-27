import cv2
import numpy as np
import os
import time
from collections import defaultdict
from deepface import DeepFace
from scipy.spatial.distance import cosine
from tqdm import tqdm
from bisect import bisect_left
import subprocess # Для вызова ffmpeg

# --- Класс VirtualCamera (без изменений) ---
class VirtualCamera:
    def __init__(self, output_width, output_height,
                 target_head_height_ratio, target_head_pos_x_ratio, target_head_pos_y_ratio,
                 smoothing_factor_pos, smoothing_factor_size,
                 comfort_zone_size_delta_r, comfort_zone_pos_x_delta_r, comfort_zone_pos_y_delta_r,
                 original_frame_w, original_frame_h):
        self.output_width = output_width
        self.output_height = output_height
        self.target_head_height_ratio = target_head_height_ratio
        self.target_head_pos_x_ratio = target_head_pos_x_ratio
        self.target_head_pos_y_ratio = target_head_pos_y_ratio
        self.smoothing_factor_pos = smoothing_factor_pos
        self.smoothing_factor_size = smoothing_factor_size

        self.comfort_min_head_h_ratio = target_head_height_ratio - comfort_zone_size_delta_r
        self.comfort_max_head_h_ratio = target_head_height_ratio + comfort_zone_size_delta_r
        self.comfort_min_pos_x_ratio = target_head_pos_x_ratio - comfort_zone_pos_x_delta_r
        self.comfort_max_pos_x_ratio = target_head_pos_x_ratio + comfort_zone_pos_x_delta_r
        self.comfort_min_pos_y_ratio = target_head_pos_y_ratio - comfort_zone_pos_y_delta_r
        self.comfort_max_pos_y_ratio = target_head_pos_y_ratio + comfort_zone_pos_y_delta_r

        self.orig_frame_w = original_frame_w
        self.orig_frame_h = original_frame_h
        self.output_aspect_ratio = self.output_width / self.output_height

        self.current_cam_center_x_on_orig = self.orig_frame_w / 2
        self.current_cam_center_y_on_orig = self.orig_frame_h / 2
        orig_aspect_ratio = self.orig_frame_w / self.orig_frame_h
        if orig_aspect_ratio > self.output_aspect_ratio:
            self.current_cam_height_on_orig = float(self.orig_frame_h)
            self.current_cam_width_on_orig = self.current_cam_height_on_orig * self.output_aspect_ratio
        else:
            self.current_cam_width_on_orig = float(self.orig_frame_w)
            self.current_cam_height_on_orig = self.current_cam_width_on_orig / self.output_aspect_ratio
        
        self.initialized = False

    def reset_to_default_or_last_known(self, last_known_bbox_on_orig=None):
        if last_known_bbox_on_orig and len(last_known_bbox_on_orig) == 4 and \
           last_known_bbox_on_orig[2] > 0 and last_known_bbox_on_orig[3] > 0:
            self.initialized = False 
            self.update(last_known_bbox_on_orig, force_initial_snap=True)
        else: 
            self.current_cam_center_x_on_orig = self.orig_frame_w / 2
            self.current_cam_center_y_on_orig = self.orig_frame_h / 2
            orig_aspect_ratio = self.orig_frame_w / self.orig_frame_h
            if orig_aspect_ratio > self.output_aspect_ratio:
                self.current_cam_height_on_orig = float(self.orig_frame_h)
                self.current_cam_width_on_orig = self.current_cam_height_on_orig * self.output_aspect_ratio
            else:
                self.current_cam_width_on_orig = float(self.orig_frame_w)
                self.current_cam_height_on_orig = self.current_cam_width_on_orig / self.output_aspect_ratio
            self.initialized = False

    def _get_head_params_on_output_if_static(self, head_bbox_on_orig, current_crop_x1, current_crop_y1, current_crop_w, current_crop_h):
        if head_bbox_on_orig is None or current_crop_w < 1e-5 or current_crop_h < 1e-5:
            return None
        hx, hy, hw, hh = head_bbox_on_orig
        if hw <= 0 or hh <= 0: return None
        head_cx_orig = hx + hw / 2
        head_cy_orig = hy + hh / 2
        head_cx_in_crop_norm = (head_cx_orig - current_crop_x1) / current_crop_w
        head_cy_in_crop_norm = (head_cy_orig - current_crop_y1) / current_crop_h
        head_h_ratio_on_output = hh / current_crop_h
        return head_h_ratio_on_output, head_cx_in_crop_norm, head_cy_in_crop_norm

    def update(self, head_bbox_on_orig, force_initial_snap=False):
        target_smooth_cam_center_x = self.current_cam_center_x_on_orig
        target_smooth_cam_center_y = self.current_cam_center_y_on_orig
        target_smooth_cam_w = self.current_cam_width_on_orig
        target_smooth_cam_h = self.current_cam_height_on_orig

        has_valid_bbox = False
        if head_bbox_on_orig is not None and len(head_bbox_on_orig) == 4:
            hx, hy, hw, hh = head_bbox_on_orig
            if hw > 0 and hh > 0:
                has_valid_bbox = True
                head_center_x_orig = hx + hw / 2
                head_center_y_orig = hy + hh / 2
                
                ideal_target_cam_h_on_orig_calc = hh / self.target_head_height_ratio
                ideal_target_cam_w_on_orig_calc = ideal_target_cam_h_on_orig_calc * self.output_aspect_ratio
                offset_x_ideal = ideal_target_cam_w_on_orig_calc * (0.5 - self.target_head_pos_x_ratio)
                offset_y_ideal = ideal_target_cam_h_on_orig_calc * (0.5 - self.target_head_pos_y_ratio)
                ideal_cam_center_x_on_orig = head_center_x_orig - offset_x_ideal
                ideal_cam_center_y_on_orig = head_center_y_orig - offset_y_ideal
                ideal_target_cam_w_on_orig = ideal_target_cam_w_on_orig_calc
                ideal_target_cam_h_on_orig = ideal_target_cam_h_on_orig_calc

                needs_adjustment = False
                if not self.initialized or force_initial_snap:
                    needs_adjustment = True
                else:
                    current_crop_x1_f, current_crop_y1_f, current_crop_w_f, current_crop_h_f = self._get_crop_region_float()
                    head_params_on_output = self._get_head_params_on_output_if_static(
                        head_bbox_on_orig, current_crop_x1_f, current_crop_y1_f, current_crop_w_f, current_crop_h_f
                    )
                    if head_params_on_output:
                        h_h_out, h_cx_out, h_cy_out = head_params_on_output
                        if not (self.comfort_min_head_h_ratio <= h_h_out <= self.comfort_max_head_h_ratio and
                                self.comfort_min_pos_x_ratio <= h_cx_out <= self.comfort_max_pos_x_ratio and
                                self.comfort_min_pos_y_ratio <= h_cy_out <= self.comfort_max_pos_y_ratio):
                            needs_adjustment = True
                    else: needs_adjustment = True 
                
                if needs_adjustment:
                    target_smooth_cam_center_x = ideal_cam_center_x_on_orig
                    target_smooth_cam_center_y = ideal_cam_center_y_on_orig
                    target_smooth_cam_w = ideal_target_cam_w_on_orig
                    target_smooth_cam_h = ideal_target_cam_h_on_orig

        if not has_valid_bbox and not self.initialized:
            return

        if not self.initialized and has_valid_bbox:
            self.current_cam_center_x_on_orig = target_smooth_cam_center_x
            self.current_cam_center_y_on_orig = target_smooth_cam_center_y
            self.current_cam_width_on_orig = target_smooth_cam_w
            self.current_cam_height_on_orig = target_smooth_cam_h
            self.initialized = True
        elif self.initialized:
            sf_pos = self.smoothing_factor_pos
            sf_size = self.smoothing_factor_size
            self.current_cam_center_x_on_orig = (1 - sf_pos) * self.current_cam_center_x_on_orig + sf_pos * target_smooth_cam_center_x
            self.current_cam_center_y_on_orig = (1 - sf_pos) * self.current_cam_center_y_on_orig + sf_pos * target_smooth_cam_center_y
            self.current_cam_width_on_orig = (1 - sf_size) * self.current_cam_width_on_orig + sf_size * target_smooth_cam_w
            self.current_cam_height_on_orig = (1 - sf_size) * self.current_cam_height_on_orig + sf_size * target_smooth_cam_h

        self.current_cam_width_on_orig = max(1.0, self.current_cam_width_on_orig)
        self.current_cam_height_on_orig = max(1.0, self.current_cam_height_on_orig)
        self.current_cam_width_on_orig = min(self.current_cam_width_on_orig, float(self.orig_frame_w))
        self.current_cam_height_on_orig = min(self.current_cam_height_on_orig, float(self.orig_frame_h))

        current_aspect_calc = self.current_cam_width_on_orig / self.current_cam_height_on_orig
        if abs(current_aspect_calc - self.output_aspect_ratio) > 0.01:
            if current_aspect_calc > self.output_aspect_ratio:
                self.current_cam_width_on_orig = self.current_cam_height_on_orig * self.output_aspect_ratio
            else:
                self.current_cam_height_on_orig = self.current_cam_width_on_orig / self.output_aspect_ratio
        
        half_w_final = self.current_cam_width_on_orig / 2
        half_h_final = self.current_cam_height_on_orig / 2
        self.current_cam_center_x_on_orig = np.clip(self.current_cam_center_x_on_orig, half_w_final, self.orig_frame_w - half_w_final)
        self.current_cam_center_y_on_orig = np.clip(self.current_cam_center_y_on_orig, half_h_final, self.orig_frame_h - half_h_final)

    def _get_crop_region_float(self):
        x1 = self.current_cam_center_x_on_orig - self.current_cam_width_on_orig / 2
        y1 = self.current_cam_center_y_on_orig - self.current_cam_height_on_orig / 2
        return x1, y1, self.current_cam_width_on_orig, self.current_cam_height_on_orig

    def get_crop_region(self):
        x1, y1, w, h = self._get_crop_region_float()
        x1_int, y1_int = int(round(x1)), int(round(y1))
        x2_int = int(round(x1 + w))
        y2_int = int(round(y1 + h))
        w_int = x2_int - x1_int
        h_int = y2_int - y1_int
        x1_int = max(0, x1_int)
        y1_int = max(0, y1_int)
        if x1_int + w_int > self.orig_frame_w: w_int = self.orig_frame_w - x1_int
        if y1_int + h_int > self.orig_frame_h: h_int = self.orig_frame_h - y1_int
        w_int = max(1, w_int)
        h_int = max(1, h_int)
        return x1_int, y1_int, w_int, h_int

# --- Вспомогательные функции (могут быть вынесены или остаться внутри process_video) ---
def _get_recognition_threshold(model_name, base_threshold, distance_metric="cosine"):
    thresholds = {
        "VGG-Face": {"cosine": 0.40, "euclidean_l2": 0.86}, "Facenet": {"cosine": 0.40, "euclidean_l2": 0.80},
        "Facenet512": {"cosine": 0.30, "euclidean_l2": 1.04}, "ArcFace": {"cosine": 0.68, "euclidean_l2": 1.13},
        "Dlib": {"cosine": 0.07, "euclidean_l2": 0.4}, "SFace": {"cosine": 0.593, "euclidean_l2": 1.055},
        "OpenFace": {"cosine": 0.10, "euclidean_l2": 0.55}, "DeepFace": {"cosine": 0.23, "euclidean_l2": 0.64},
        "DeepID": {"cosine": 0.015, "euclidean_l2": 0.17}, "GhostFaceNet": {"cosine": 0.40, "euclidean_l2": 0.86},
    }
    if model_name in thresholds and distance_metric in thresholds[model_name]:
        if distance_metric == "cosine":
            return 1.0 - thresholds[model_name][distance_metric]
        else:
            print(f"Предупреждение: Используется метрика {distance_metric} для {model_name}. Порог {base_threshold} может быть неоптимален.")
            return base_threshold
    print(f"Предупреждение: Модель {model_name} или метрика {distance_metric} не найдены в таблице порогов. Используется базовый порог {base_threshold}.")
    return base_threshold

def _analyze_video_for_tracks(video_path, original_fps, total_frames_video, original_frame_w, original_frame_h,
                              recognition_model_name, detector_backend, final_similarity_threshold,
                              fps_to_process_analysis, analysis_max_width,
                              teleport_max_frame_diff_analysis, teleport_min_distance_ratio, teleport_min_area_change_ratio):
    print("\n--- Этап 1: Анализ видео и сбор треков (с детектором телепортации) ---")
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"Ошибка: Не удалось открыть {video_path}")
        return None

    face_tracks_data = {} 
    next_face_id = 0
    process_every_nth_frame_for_analysis = max(1, int(round(original_fps / fps_to_process_analysis)))
    
    #print("Разогрев DeepFace...")
    #dummy_frame = np.zeros((100, 100, 3), dtype=np.uint8)
    #try: 
    #    DeepFace.represent(img_path=dummy_frame, model_name=recognition_model_name,
    #                       detector_backend=detector_backend, enforce_detection=False, align=True)
    #    print("DeepFace готов.")
    #except Exception as e: 
    #    print(f"Ошибка разогрева DeepFace: {e}. Попытка продолжить...")
    # Примечание: Разогрев может быть долгим, и его можно опционально выключить или делать более умным.
    # Для функции лучше, чтобы он был внутри или явно контролировался. Пока убрал для скорости тестов.

    processed_frame_count_analysis = 0
    num_frames_to_actually_analyze = total_frames_video // process_every_nth_frame_for_analysis
    
    with tqdm(total=num_frames_to_actually_analyze, desc="Анализ видео") as pbar_analysis:
        for current_frame_num_in_video in range(total_frames_video):
            ret, frame_orig = cap.read()
            if not ret: break
            if current_frame_num_in_video % process_every_nth_frame_for_analysis != 0: continue
            
            pbar_analysis.update(1)
            processed_frame_count_analysis += 1
            frame_to_analyze = frame_orig.copy()
            scale_factor = 1.0

            if analysis_max_width and original_frame_w > analysis_max_width:
                scale_factor = analysis_max_width / original_frame_w
                new_height = int(original_frame_h * scale_factor)
                frame_to_analyze = cv2.resize(frame_orig, (analysis_max_width, new_height), interpolation=cv2.INTER_AREA)
            
            try: 
                embedding_objs = DeepFace.represent(
                    img_path=frame_to_analyze, model_name=recognition_model_name,
                    detector_backend=detector_backend, align=True, enforce_detection=False 
                )
            except Exception: embedding_objs = []
            
            current_detections_on_frame = []
            if isinstance(embedding_objs, list):
                for obj in embedding_objs:
                    if isinstance(obj, dict) and 'facial_area' in obj and 'embedding' in obj:
                        fa = obj['facial_area']
                        if isinstance(fa, dict) and fa.get('w', 0) > 0 and fa.get('h', 0) > 0:
                            x_s, y_s, w_s, h_s = fa['x'], fa['y'], fa['w'], fa['h']
                            bbox_orig_coords = (
                                int(x_s / scale_factor), int(y_s / scale_factor),
                                int(w_s / scale_factor), int(h_s / scale_factor)
                            )
                            current_detections_on_frame.append({'bbox': bbox_orig_coords, 'embedding': obj['embedding']})
            
            matched_track_ids_this_frame = set()
            for det_face in current_detections_on_frame:
                new_emb, new_bbox = np.array(det_face['embedding']), det_face['bbox']
                best_sim, found_track_id = -1.0, -1
                for track_id, track_data_loop in face_tracks_data.items():
                    if track_id in matched_track_ids_this_frame: continue
                    last_emb_val = track_data_loop.get('last_embedding')
                    if last_emb_val is None: continue # Трек мог быть только что создан без эмбеддинга
                    try:
                        similarity = 1 - cosine(new_emb, np.array(last_emb_val))
                    except Exception as e:
                        # print(f"Ошибка cosine similarity: {e}. Emb new: {new_emb.shape}, Emb last: {np.array(last_emb_val).shape}")
                        similarity = -1.0 # Не удалось сравнить
                        
                    if similarity > best_sim and similarity >= final_similarity_threshold:
                        best_sim, found_track_id = similarity, track_id
                
                if found_track_id != -1:
                    track_data = face_tracks_data[found_track_id]
                    prev_bbox_for_teleport = track_data.get('last_bbox')
                    prev_proc_frame_idx_for_teleport = track_data.get('last_processed_frame_idx')
                    
                    track_data.update({
                        'last_bbox': new_bbox, 'last_embedding': new_emb,
                        'missed_frames_streak': 0, 'last_processed_frame_idx': processed_frame_count_analysis
                    })
                    track_data['frame_to_bbox_map'][current_frame_num_in_video] = new_bbox
                    matched_track_ids_this_frame.add(found_track_id)

                    is_teleport = False
                    if prev_bbox_for_teleport and prev_proc_frame_idx_for_teleport is not None:
                        frame_diff_analysis = processed_frame_count_analysis - prev_proc_frame_idx_for_teleport
                        if 0 < frame_diff_analysis <= teleport_max_frame_diff_analysis:
                            px, py, pw, ph = prev_bbox_for_teleport
                            nx, ny, nw, nh = new_bbox
                            if pw > 0 and ph > 0 and nw > 0 and nh > 0:
                                prev_cx, prev_cy = px + pw/2, py + ph/2
                                new_cx, new_cy = nx + nw/2, ny + nh/2
                                dist_x_abs, dist_y_abs = abs(prev_cx - new_cx), abs(prev_cy - new_cy)
                                
                                distance_teleport_cond = False
                                if original_frame_w > 0 and original_frame_h > 0:
                                    if (dist_x_abs / original_frame_w > teleport_min_distance_ratio or \
                                        dist_y_abs / original_frame_h > teleport_min_distance_ratio):
                                        distance_teleport_cond = True
                                
                                area_teleport_cond = False
                                prev_area, new_area = float(pw * ph), float(nw * nh)
                                if prev_area > 1e-5 and new_area > 1e-5:
                                    if max(prev_area, new_area) / min(prev_area, new_area) > teleport_min_area_change_ratio:
                                        area_teleport_cond = True
                                if distance_teleport_cond or area_teleport_cond:
                                    is_teleport = True
                    if is_teleport:
                        track_data['teleport_frames'].append(current_frame_num_in_video)
                else: 
                    next_face_id += 1
                    face_tracks_data[next_face_id] = {
                        'id': next_face_id, 'last_bbox': new_bbox, 'last_embedding': new_emb,
                        'frame_to_bbox_map': {current_frame_num_in_video: new_bbox},
                        'missed_frames_streak': 0, 
                        'start_processed_frame_idx': processed_frame_count_analysis, 
                        'last_processed_frame_idx': processed_frame_count_analysis,
                        'teleport_frames': [] 
                    }
                    matched_track_ids_this_frame.add(next_face_id)

            for track_id_iter in list(face_tracks_data.keys()): 
                if track_id_iter not in matched_track_ids_this_frame:
                    if face_tracks_data[track_id_iter]['last_processed_frame_idx'] < processed_frame_count_analysis:
                         face_tracks_data[track_id_iter]['missed_frames_streak'] += 1
    cap.release()
    print(f"Этап 1 завершен. Найдено треков: {len(face_tracks_data)}")
    return face_tracks_data

def _select_main_speakers(face_tracks_data, original_fps, 
                          min_track_duration_seconds, fps_to_process_analysis, max_frames_to_keep_track_without_detection,
                          autodetect_speaker_count, top_n_faces_to_crop, 
                          autodetect_significant_drop_ratio, autodetect_min_speakers, autodetect_max_speakers):
    print("\n--- Этап 2: Выбор главных спикеров ---")
    valid_speakers_intermediate = []
    min_detections_for_track = min_track_duration_seconds * fps_to_process_analysis 
    
    for track_id, data in face_tracks_data.items():
        num_bbox_entries = len(data.get('frame_to_bbox_map', {}))
        if num_bbox_entries >= min_detections_for_track:
            # Более мягкая проверка на пропуски в конце, чтобы не отсеять активных, но временно потерянных
            if data['missed_frames_streak'] <= max_frames_to_keep_track_without_detection * 3: 
                 valid_speakers_intermediate.append({'id': track_id, 'num_detections': num_bbox_entries, 'data': data})
                 print(f"  Трек ID {track_id} прошел первичный отбор. Детекций: {num_bbox_entries}, пропусков в конце: {data['missed_frames_streak']}, телепортов: {len(data.get('teleport_frames',[]))}")
            else:
                 print(f"  Трек ID {track_id} отброшен (слишком много пропусков в конце: {data['missed_frames_streak']})")
        else:
            print(f"  Трек ID {track_id} отброшен (слишком короткий: {num_bbox_entries} < {min_detections_for_track})")
    
    valid_speakers_intermediate.sort(key=lambda x: x['num_detections'], reverse=True)
    
    selected_speakers = []
    if not autodetect_speaker_count:
        selected_speakers = valid_speakers_intermediate[:top_n_faces_to_crop]
        print(f"\nВыбрано (ручной режим TOP_N): {len(selected_speakers)} спикеров.")
    else:
        if not valid_speakers_intermediate:
            selected_speakers = []
        elif len(valid_speakers_intermediate) == 1:
            selected_speakers = valid_speakers_intermediate[:autodetect_max_speakers] # Учитываем max, даже если один
        else:
            num_to_take_by_drop = len(valid_speakers_intermediate) 
            for i in range(1, len(valid_speakers_intermediate)):
                prev_detections = valid_speakers_intermediate[i-1]['num_detections']
                current_detections = valid_speakers_intermediate[i]['num_detections']
                ratio = (current_detections / prev_detections) if prev_detections > 0 else 0
                
                if ratio < autodetect_significant_drop_ratio:
                    num_to_take_by_drop = i 
                    break
            
            final_num_to_take = max(autodetect_min_speakers, num_to_take_by_drop)
            final_num_to_take = min(final_num_to_take, autodetect_max_speakers)
            final_num_to_take = min(final_num_to_take, len(valid_speakers_intermediate))
            
            selected_speakers = valid_speakers_intermediate[:final_num_to_take]
        print(f"\nВыбрано (автоматический режим): {len(selected_speakers)} спикеров.")

    for sp in selected_speakers: 
        print(f"  Спикер ID: {sp['id']}, Детекций: {sp['num_detections']}")
    return selected_speakers

def _get_interpolated_bbox(current_frame_num, sorted_known_frames, frame_to_bbox_map, last_resort_bbox):
    if not sorted_known_frames: return last_resort_bbox
    if current_frame_num in frame_to_bbox_map: return frame_to_bbox_map[current_frame_num]
    idx = bisect_left(sorted_known_frames, current_frame_num)
    if idx == 0: return frame_to_bbox_map.get(sorted_known_frames[0], last_resort_bbox)
    if idx == len(sorted_known_frames): return frame_to_bbox_map.get(sorted_known_frames[-1], last_resort_bbox)
    prev_known_frame, next_known_frame = sorted_known_frames[idx - 1], sorted_known_frames[idx]
    bbox_prev, bbox_next = frame_to_bbox_map.get(prev_known_frame), frame_to_bbox_map.get(next_known_frame)
    if bbox_prev is None or bbox_next is None or len(bbox_prev) != 4 or len(bbox_next) != 4 : return last_resort_bbox
    bbox_prev_f, bbox_next_f = np.array(bbox_prev, dtype=float), np.array(bbox_next, dtype=float)
    if next_known_frame == prev_known_frame: return bbox_prev_f.astype(int).tolist()
    ratio = (float(current_frame_num) - prev_known_frame) / (next_known_frame - prev_known_frame)
    return (bbox_prev_f + ratio * (bbox_next_f - bbox_prev_f)).astype(int).tolist()

def _generate_speaker_videos(video_path, selected_speakers, original_fps, total_frames_video, original_frame_w, original_frame_h,
                             output_save_dir,
                             cam_output_width, cam_output_height, cam_target_head_height_ratio, 
                             cam_target_head_pos_x_ratio, cam_target_head_pos_y_ratio,
                             cam_smoothing_factor_pos, cam_smoothing_factor_size,
                             cam_comfort_zone_size_delta_r, cam_comfort_zone_pos_x_delta_r, cam_comfort_zone_pos_y_delta_r,
                             output_video_fps_factor, output_video_codec, add_audio):
    print("\n--- Этап 3: Генерация видео для спикеров ---")
    os.makedirs(output_save_dir, exist_ok=True)
    
    generated_video_paths_no_audio = []

    for speaker_info in tqdm(selected_speakers, desc="Обработка спикеров", unit="спикер"):
        speaker_id = speaker_info['id']
        speaker_data = speaker_info['data']
        frame_to_bbox_map_for_speaker = speaker_data['frame_to_bbox_map']
        teleport_frames_for_speaker = set(speaker_data.get('teleport_frames', []))

        if not frame_to_bbox_map_for_speaker:
            print(f"Предупреждение: Нет bbox для спикера ID {speaker_id}. Пропуск.")
            continue

        sorted_known_frames_for_speaker = sorted(frame_to_bbox_map_for_speaker.keys())
        if not sorted_known_frames_for_speaker: 
            print(f"Предупреждение: sorted_known_frames_for_speaker пуст для спикера ID {speaker_id}. Пропуск.")
            continue

        vcam = VirtualCamera(cam_output_width, cam_output_height,
                             cam_target_head_height_ratio, cam_target_head_pos_x_ratio, cam_target_head_pos_y_ratio,
                             cam_smoothing_factor_pos, cam_smoothing_factor_size,
                             cam_comfort_zone_size_delta_r, cam_comfort_zone_pos_x_delta_r, cam_comfort_zone_pos_y_delta_r,
                             original_frame_w, original_frame_h)
        
        first_known_bbox = frame_to_bbox_map_for_speaker[sorted_known_frames_for_speaker[0]]
        vcam.reset_to_default_or_last_known(first_known_bbox)

        base_filename = f"speaker_{speaker_id}_cropped"
        output_filename_no_audio = os.path.join(output_save_dir, f"{base_filename}_no_audio.mp4")
        
        writer = cv2.VideoWriter(output_filename_no_audio, cv2.VideoWriter_fourcc(*output_video_codec),
                                 original_fps * output_video_fps_factor, 
                                 (cam_output_width, cam_output_height))
        
        cap_process = cv2.VideoCapture(video_path)
        last_resort_bbox_for_cam = first_known_bbox 

        print(f"\nГенерация для спикера ID {speaker_id} -> {output_filename_no_audio}")
        for current_frame_num_in_video in tqdm(range(total_frames_video), desc=f"  Кадры спикера {speaker_id}", leave=False, unit="кадр"):
            ret, frame_orig = cap_process.read()
            if not ret: break
            
            bbox_to_use_for_cam = _get_interpolated_bbox(
                current_frame_num_in_video, sorted_known_frames_for_speaker,
                frame_to_bbox_map_for_speaker, last_resort_bbox_for_cam 
            )
            
            bbox_is_valid_for_vcam = False
            if bbox_to_use_for_cam and len(bbox_to_use_for_cam) == 4 and \
               bbox_to_use_for_cam[2] > 0 and bbox_to_use_for_cam[3] > 0:
                last_resort_bbox_for_cam = bbox_to_use_for_cam
                bbox_is_valid_for_vcam = True
            
            is_teleport_frame = current_frame_num_in_video in teleport_frames_for_speaker
            
            if is_teleport_frame and bbox_is_valid_for_vcam:
                vcam.reset_to_default_or_last_known(bbox_to_use_for_cam)
            else:
                vcam.update(bbox_to_use_for_cam if bbox_is_valid_for_vcam else None)
            
            output_frame = np.zeros((cam_output_height, cam_output_width, 3), dtype=np.uint8)
            if vcam.initialized:
                crop_x, crop_y, crop_w, crop_h = vcam.get_crop_region()
                if crop_w > 0 and crop_h > 0:
                    crop_y_end, crop_x_end = min(crop_y + crop_h, original_frame_h), min(crop_x + crop_w, original_frame_w)
                    if crop_y_end > crop_y and crop_x_end > crop_x :
                        cropped_content = frame_orig[crop_y:crop_y_end, crop_x:crop_x_end]
                        if cropped_content.size > 0:
                            interpolation_method = cv2.INTER_LANCZOS4 if (crop_w * crop_h > cam_output_width * cam_output_height) else cv2.INTER_LINEAR
                            try:
                                output_frame = cv2.resize(cropped_content, (cam_output_width, cam_output_height), interpolation=interpolation_method)
                            except cv2.error as e:
                                print(f"Ошибка cv2.resize (ID {speaker_id}, кадр {current_frame_num_in_video}): {e}. Cropped shape: {cropped_content.shape}, crop_region: {(crop_x,crop_y,crop_w,crop_h)}")
            writer.write(output_frame)
            
        writer.release()
        cap_process.release()
        generated_video_paths_no_audio.append(output_filename_no_audio)
        print(f"  Видео без звука для спикера ID {speaker_id} сохранено.")
    
    return generated_video_paths_no_audio

def _add_audio_to_videos(input_video_path, video_paths_no_audio, output_save_dir):
    print("\n--- Этап 4: Добавление аудио к сгенерированным видео ---")
    if not video_paths_no_audio:
        print("Нет видео для добавления аудио. Пропуск.")
        return

    # 1. Извлечь аудио из исходного видео
    base_input_filename = os.path.splitext(os.path.basename(input_video_path))[0]
    temp_audio_path = os.path.join(output_save_dir, f"{base_input_filename}_extracted_audio.aac") # Используем aac для хорошего качества
    
    # Проверяем, есть ли аудиодорожка в исходном видео
    try:
        ffprobe_cmd = [
            'ffprobe', '-v', 'error', '-select_streams', 'a:0', 
            '-show_entries', 'stream=codec_type', '-of', 'default=nw=1:nk=1', input_video_path
        ]
        result = subprocess.run(ffprobe_cmd, capture_output=True, text=True, check=True)
        if not result.stdout.strip(): # Если вывод пустой, значит аудиодорожки нет
            print(f"В исходном видео {input_video_path} не найдена аудиодорожка. Пропуск добавления аудио.")
            # Удаляем временные файлы, если они были созданы другими путями
            for video_no_audio_path in video_paths_no_audio:
                 final_video_path = video_no_audio_path.replace("_no_audio.mp4", "_with_audio.mp4")
                 if os.path.exists(final_video_path): os.remove(final_video_path) # Удаляем старый, если есть
                 os.rename(video_no_audio_path, final_video_path) # Переименовываем _no_audio в _with_audio
                 print(f"  Файл {video_no_audio_path} переименован в {final_video_path} (без добавления аудио).")
            return
    except subprocess.CalledProcessError as e:
        print(f"Ошибка при проверке аудиодорожки в {input_video_path} с помощью ffprobe: {e}. Пропуск добавления аудио.")
        # Аналогично переименовываем
        for video_no_audio_path in video_paths_no_audio:
             final_video_path = video_no_audio_path.replace("_no_audio.mp4", "_with_audio.mp4")
             if os.path.exists(final_video_path): os.remove(final_video_path)
             os.rename(video_no_audio_path, final_video_path)
             print(f"  Файл {video_no_audio_path} переименован в {final_video_path} (без добавления аудио).")
        return
    except FileNotFoundError:
        print("Ошибка: ffprobe не найден. Убедитесь, что ffmpeg (и ffprobe) установлен и доступен в PATH. Пропуск добавления аудио.")
        for video_no_audio_path in video_paths_no_audio:
             final_video_path = video_no_audio_path.replace("_no_audio.mp4", "_with_audio.mp4")
             if os.path.exists(final_video_path): os.remove(final_video_path)
             os.rename(video_no_audio_path, final_video_path)
             print(f"  Файл {video_no_audio_path} переименован в {final_video_path} (без добавления аудио).")
        return


    extract_audio_cmd = [
        'ffmpeg', '-y', '-i', input_video_path, 
        '-vn', '-acodec', 'copy', temp_audio_path
    ]
    print(f"Извлечение аудио: {' '.join(extract_audio_cmd)}")
    try:
        subprocess.run(extract_audio_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        print(f"Аудио извлечено в {temp_audio_path}")
    except subprocess.CalledProcessError as e:
        print(f"Ошибка извлечения аудио: {e.stderr.decode() if e.stderr else e}")
        print("Продолжение без добавления аудио. Видео останутся без звука (_no_audio.mp4).")
        return # Если аудио не извлеклось, нет смысла продолжать
    except FileNotFoundError:
        print("Ошибка: ffmpeg не найден. Убедитесь, что ffmpeg установлен и доступен в PATH. Пропуск добавления аудио.")
        return

    # 2. Добавить извлеченное аудио к каждому видео
    for video_no_audio_path in video_paths_no_audio:
        final_video_path = video_no_audio_path.replace("_no_audio.mp4", "_with_audio.mp4")
        
        merge_cmd = [
            'ffmpeg', '-y', 
            '-i', video_no_audio_path, 
            '-i', temp_audio_path,
            '-c:v', 'copy',      # Копировать видеопоток без перекодирования
            '-c:a', 'aac',       # Перекодировать аудио в AAC (стандарт)
            '-shortest',         # Обрезать выходной файл по длине самого короткого потока (видео или аудио)
            final_video_path
        ]
        print(f"Слияние аудио и видео: {' '.join(merge_cmd)}")
        try:
            subprocess.run(merge_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            print(f"Финальное видео сохранено: {final_video_path}")
            os.remove(video_no_audio_path) # Удаляем версию без звука
        except subprocess.CalledProcessError as e:
            print(f"Ошибка слияния для {final_video_path}: {e.stderr.decode() if e.stderr else e}")
            print(f"  Видео {video_no_audio_path} останется без звука.")
        except FileNotFoundError:
            print("Ошибка: ffmpeg не найден во время слияния. Пропуск.")
            # Не удаляем video_no_audio_path в этом случае
            
    # 3. Удалить временный аудиофайл
    if os.path.exists(temp_audio_path):
        try:
            os.remove(temp_audio_path)
            print(f"Временный аудиофайл {temp_audio_path} удален.")
        except OSError as e:
            print(f"Не удалось удалить временный аудиофайл {temp_audio_path}: {e}")


def process_video_for_speaker_cuts(
    # --- Основные пути ---
    input_video_path: str,
    output_save_dir: str = "./speaker_cuts_output/",
    
    # --- Параметры DeepFace для распознавания лиц ---
    recognition_model_name: str = "Facenet512", # Модель для генерации эмбеддингов (векторов) лиц.
                                                # "Facenet512" - хороший баланс точности и скорости.
    detector_backend: str = "mtcnn",           # Детектор лиц (находит лица на кадрах). 
                                                # "mtcnn" - точный, но может быть медленным. Другие: "opencv", "ssd", "retinaface".
    similarity_threshold_base: float = 0.68,   # Базовый порог СХОДСТВА для сравнения лиц (1.0 - макс. сходство).
                                                # Используется, если для выбранной `recognition_model_name` нет предопределенного порога.
                                                # Для косинусного расстояния DeepFace обычно возвращает пороги расстояния, 
                                                # а мы здесь используем (1 - расстояние).

    # --- Параметры анализа видео (поиск треков лиц) ---
    fps_to_process_analysis: float = 1.0,      # Сколько кадров в секунду из исходного видео анализировать.
                                                # 1.0 - анализировать 1 кадр в секунду. 0.5 - 1 кадр каждые 2 секунды.
                                                # Уменьшение значения ускоряет анализ, но может пропустить короткие появления.
    analysis_max_width: int = 480,             # Максимальная ширина кадра, до которой он будет уменьшен ПЕРЕД анализом.
                                                # 0 - не уменьшать. Уменьшение ускоряет детекцию и распознавание.
                                                # Кадрирование потом будет производиться на оригинальном видео.
    max_frames_to_keep_track_without_detection_factor: float = 2.5, 
                                                # Множитель для `fps_to_process_analysis`. Определяет, как долго трек 
                                                # считается "живым" без детекции лица, прежде чем он будет считаться прерванным.
                                                # Например, если fps_to_process_analysis=1, то 2.5 => 2.5 секунды.

    # --- Параметры выбора главных спикеров ---
    min_track_duration_seconds: float = 3.0,   # Минимальная общая длительность присутствия трека (в секундах видео),
                                                # чтобы он рассматривался как кандидат в спикеры.
    autodetect_speaker_count: bool = True,     # Включить автоматическое определение количества спикеров?
                                                # True - используется логика с "разрывом" и мин/макс количеством.
                                                # False - используется `top_n_faces_to_crop`.
    top_n_faces_to_crop: int = 2,              # Если `autodetect_speaker_count = False`, то сколько
                                                # самых "долгоиграющих" спикеров выбрать.
    autodetect_significant_drop_ratio: float = 0.6, 
                                                # (Если автодетекция включена) Коэффициент для определения "значительного разрыва"
                                                # в длительности присутствия спикеров. Если следующий спикер имеет менее
                                                # X% времени предыдущего, считается разрывом. (0.0 до 1.0)
    autodetect_min_speakers: int = 1,          # (Если автодетекция включена) Минимальное количество спикеров,
                                                # которое выберет автоматика (если хоть кто-то прошел порог `min_track_duration_seconds`).
    autodetect_max_speakers: int = 3,          # (Если автодетекция включена) Максимальное количество спикеров,
                                                # которое выберет автоматика.

    # --- Параметры детекции "телепортации" спикера (для резких монтажных склеек) ---
    teleport_max_frame_diff_analysis: int = 2, # Максимальное количество ПРОАНАЛИЗИРОВАННЫХ кадров между двумя детекциями
                                                # одного лица, чтобы скачок считался "мгновенным" (кандидат на телепорт).
    teleport_min_distance_ratio: float = 0.25, # Минимальное смещение центра bounding box'а лица (в долях от ширины/высоты
                                                # ИСХОДНОГО кадра) для срабатывания условия телепорта по расстоянию.
    teleport_min_area_change_ratio: float = 2.5, 
                                                # Минимальное изменение площади bounding box'а лица (во сколько раз, например,
                                                # в 2.5 раза больше или меньше) для срабатывания условия телепорта по размеру.

    # --- Параметры виртуальной камеры (кадрирование) ---
    cam_output_width: int = 1080,              # Ширина выходного кадра для каждого спикера.
    cam_output_height: int = 1920,             # Высота выходного кадра (портретный режим для Shorts/TikTok/Reels).
    cam_target_head_height_ratio: float = 0.35, # Целевая высота головы спикера на ВЫХОДНОМ кадре (доля от высоты кадра).
                                                # Например, 0.35 означает, что голова будет занимать 35% высоты кадра.
    cam_target_head_pos_x_ratio: float = 0.5,  # Целевая позиция центра головы по X на ВЫХОДНОМ кадре (0.0 - лево, 1.0 - право).
    cam_target_head_pos_y_ratio: float = 0.5,  # Целевая позиция центра головы по Y на ВЫХОДНОМ кадре (0.0 - верх, 1.0 - низ).
                                                # 0.5 - центр, можно сделать < 0.5 для "говорящей головы" (ближе к верху).
    cam_smoothing_factor_position: float = 0.1,# Коэффициент сглаживания для ДВИЖЕНИЯ камеры (0.0 до 1.0).
                                                # Меньше -> более плавно, но медленнее реакция.
    cam_smoothing_factor_size: float = 0.1,    # Коэффициент сглаживания для МАСШТАБИРОВАНИЯ камеры (0.0 до 1.0).
                                                # Меньше -> более плавно.
    # Параметры "зоны комфорта" виртуальной камеры (доли от размеров ВЫХОДНОГО кадра)
    cam_comfort_zone_size_delta_r: float = 0.07, # Насколько РАЗМЕР головы на выходном кадре может отклоняться от целевого
                                                 # (в +/- долях от `cam_target_head_height_ratio`), прежде чем камера начнет коррекцию.
    cam_comfort_zone_pos_x_delta_r: float = 0.07,# Насколько ПОЗИЦИЯ X головы на выходном кадре может отклоняться от целевой
                                                 # (в +/- долях от ширины выходного кадра).
    cam_comfort_zone_pos_y_delta_r: float = 0.07,# Насколько ПОЗИЦИЯ Y головы на выходном кадре может отклоняться от целевой
                                                 # (в +/- долях от высоты выходного кадра).

    # --- Параметры выходного видео ---
    output_video_fps_factor: float = 1.0,      # Множитель для FPS исходного видео. 1.0 - такой же FPS.
                                                # Можно использовать для замедления/ускорения, но обычно 1.0.
    output_video_codec: str = 'mp4v',          # Кодек для записи видеофайлов. 'mp4v' - стандартный для .mp4.
                                                # 'avc1' (H.264) может дать лучшее сжатие, если доступен.
    add_audio_to_output: bool = True           # Добавлять ли оригинальную аудиодорожку к каждому сгенерированному видео спикера.
                                                # Требует `ffmpeg` в системном PATH.
    ):
    """
    Обрабатывает видео для создания отдельных нарезок для каждого основного спикера
    с динамическим кадрированием, зоной комфорта, обработкой "телепортации"
    и опциональным автоматическим определением количества спикеров и добавлением аудио.
    
    Returns:
        tuple: (success: bool, output_files: list[str], error_message: str)
            - success: True если обработка прошла успешно
            - output_files: список путей к созданным видеофайлам
            - error_message: сообщение об ошибке (если success=False)
    """
    # --- Начало работы и проверка входных данных ---
    print(f"--- Запуск обработки видео: {input_video_path} ---")
    if not os.path.exists(input_video_path):
        error_msg = f"Ошибка: Файл не найден: {input_video_path}"
        print(error_msg)
        return False, [], error_msg

    # Получаем информацию об исходном видео (FPS, размеры, количество кадров)
    cap_info = cv2.VideoCapture(input_video_path)
    if not cap_info.isOpened():
        error_msg = f"Ошибка: Не удалось открыть {input_video_path}"
        print(error_msg)
        return False, [], error_msg
    
    original_fps = cap_info.get(cv2.CAP_PROP_FPS)
    original_fps = 30.0 if original_fps == 0 else original_fps # Запасное значение, если FPS не определен
    total_frames_video = int(cap_info.get(cv2.CAP_PROP_FRAME_COUNT))
    original_frame_w = int(cap_info.get(cv2.CAP_PROP_FRAME_WIDTH))
    original_frame_h = int(cap_info.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap_info.release() # Закрываем файл после получения информации

    # Проверка, что параметры видео корректны
    if total_frames_video <=0 or original_frame_w <=0 or original_frame_h <=0 :
        error_msg = f"Ошибка: Некорректные параметры видео: FPS={original_fps}, Frames={total_frames_video}, W={original_frame_w}, H={original_frame_h}"
        print(error_msg)
        return False, [], error_msg
        
    print(f"Параметры видео: {original_frame_w}x{original_frame_h}, {original_fps:.2f} FPS, {total_frames_video} кадров.")
    full_start_time = time.time() # Засекаем общее время выполнения

    # --- Подготовка производных параметров ---
    # Получаем порог схожести для выбранной модели DeepFace
    final_similarity_threshold = _get_recognition_threshold(recognition_model_name, similarity_threshold_base)
    print(f"Модель: {recognition_model_name}, Детектор: {detector_backend}, Порог сходства: {final_similarity_threshold:.3f}")
    if analysis_max_width > 0: # Используем > 0, так как 0 означает "не ресайзить"
        print(f"Анализ на макс. ширине: {analysis_max_width}px")

    # Рассчитываем, сколько пропущенных АНАЛИЗИРУЕМЫХ кадров трек может выдержать
    max_frames_missed_analysis_streak = int(fps_to_process_analysis * max_frames_to_keep_track_without_detection_factor)

    # --- Этап 1: Анализ видео для сбора треков лиц ---
    # Эта функция проходит по видео (с частотой `fps_to_process_analysis`),
    # находит лица, генерирует для них эмбеддинги и пытается сгруппировать
    # детекции одного и того же человека в "треки".
    # Также определяет "телепортации" для каждого трека.
    all_tracks = _analyze_video_for_tracks(
        video_path=input_video_path, 
        original_fps=original_fps, 
        total_frames_video=total_frames_video, 
        original_frame_w=original_frame_w, 
        original_frame_h=original_frame_h,
        recognition_model_name=recognition_model_name, 
        detector_backend=detector_backend, 
        final_similarity_threshold=final_similarity_threshold,
        fps_to_process_analysis=fps_to_process_analysis, 
        analysis_max_width=analysis_max_width,
        teleport_max_frame_diff_analysis=teleport_max_frame_diff_analysis, 
        teleport_min_distance_ratio=teleport_min_distance_ratio, 
        teleport_min_area_change_ratio=teleport_min_area_change_ratio
    )
    if not all_tracks: 
        error_msg = "Анализ не дал треков. Завершение."
        print(error_msg)
        return False, [], error_msg
        
    # --- Этап 2: Выбор главных спикеров ---
    # Из всех найденных треков выбираются "главные" на основе их длительности
    # и, если включена автодетекция, на основе анализа "разрывов" в длительности.
    main_speakers = _select_main_speakers(
        face_tracks_data=all_tracks, 
        original_fps=original_fps,
        min_track_duration_seconds=min_track_duration_seconds, 
        fps_to_process_analysis=fps_to_process_analysis, # Нужен для расчета min_detections
        max_frames_to_keep_track_without_detection=max_frames_missed_analysis_streak, # Передаем рассчитанное значение
        autodetect_speaker_count=autodetect_speaker_count, 
        top_n_faces_to_crop=top_n_faces_to_crop,
        autodetect_significant_drop_ratio=autodetect_significant_drop_ratio, 
        autodetect_min_speakers=autodetect_min_speakers, 
        autodetect_max_speakers=autodetect_max_speakers
    )
    if not main_speakers: 
        error_msg = "Главные спикеры не выбраны. Завершение."
        print(error_msg)
        return False, [], error_msg
        
    # --- Этап 3: Генерация видео для каждого спикера (пока без звука) ---
    # Для каждого выбранного спикера создается отдельное видео.
    # Виртуальная камера следует за спикером, применяя сглаживание,
    # зону комфорта и обработку "телепортаций".
    video_paths_no_audio = _generate_speaker_videos(
        video_path=input_video_path, 
        selected_speakers=main_speakers, 
        original_fps=original_fps, 
        total_frames_video=total_frames_video, 
        original_frame_w=original_frame_w, 
        original_frame_h=original_frame_h,
        output_save_dir=output_save_dir,
        cam_output_width=cam_output_width, 
        cam_output_height=cam_output_height, 
        cam_target_head_height_ratio=cam_target_head_height_ratio,
        cam_target_head_pos_x_ratio=cam_target_head_pos_x_ratio, 
        cam_target_head_pos_y_ratio=cam_target_head_pos_y_ratio,
        cam_smoothing_factor_pos=cam_smoothing_factor_position, 
        cam_smoothing_factor_size=cam_smoothing_factor_size,
        cam_comfort_zone_size_delta_r=cam_comfort_zone_size_delta_r, 
        cam_comfort_zone_pos_x_delta_r=cam_comfort_zone_pos_x_delta_r, 
        cam_comfort_zone_pos_y_delta_r=cam_comfort_zone_pos_y_delta_r,
        output_video_fps_factor=output_video_fps_factor, 
        output_video_codec=output_video_codec,
        add_audio=add_audio_to_output # Этот параметр здесь не используется напрямую, но передается для консистентности
    )
    
    final_output_files = []
    
    # --- Этап 4: Добавление аудио к сгенерированным видео ---
    if add_audio_to_output and video_paths_no_audio:
        # Если указано добавлять аудио и есть видео, к которым его можно добавить.
        # Использует ffmpeg для извлечения аудио из исходника и слияния с каждым видео спикера.
        _add_audio_to_videos(
            input_video_path=input_video_path, 
            video_paths_no_audio=video_paths_no_audio, 
            output_save_dir=output_save_dir
        )
        # Формируем список финальных файлов с аудио
        for video_no_audio_path in video_paths_no_audio:
            final_video_path = video_no_audio_path.replace("_no_audio.mp4", "_with_audio.mp4")
            if os.path.exists(final_video_path):
                final_output_files.append(final_video_path)
            else:
                # Если файл с аудио не создался, добавляем версию без аудио
                if os.path.exists(video_no_audio_path):
                    final_output_files.append(video_no_audio_path)
    elif not add_audio_to_output and video_paths_no_audio:
        # Если аудио добавлять не нужно, просто переименовываем файлы, убирая суффикс "_no_audio".
        print("\nПропуск добавления аудио согласно конфигурации.")
        for video_no_audio_path in video_paths_no_audio:
            # Формируем финальное имя файла без "_no_audio"
            final_video_path = video_no_audio_path.replace("_no_audio.mp4", ".mp4") 
            if os.path.exists(final_video_path) and final_video_path != video_no_audio_path: # Если файл с таким именем уже есть (и это не тот же самый файл)
                 os.remove(final_video_path) 
            try:
                if final_video_path != video_no_audio_path: # Переименовываем только если имена отличаются
                    os.rename(video_no_audio_path, final_video_path)
                    print(f"  Файл сохранен как: {final_video_path}")
                    final_output_files.append(final_video_path)
                else: # Если имя уже правильное (например, если список video_paths_no_audio уже содержит финальные имена)
                    print(f"  Файл уже существует: {final_video_path}")
                    final_output_files.append(final_video_path)
            except OSError as e:
                 print(f"Не удалось переименовать {video_no_audio_path} в {final_video_path}: {e}")
                 # Добавляем исходный файл, если переименование не удалось
                 if os.path.exists(video_no_audio_path):
                     final_output_files.append(video_no_audio_path)

    # --- Завершение ---
    print(f"\n--- Вся работа завершена за {time.time() - full_start_time:.2f} секунд ---")
    print(f"Результаты сохранены в директории: {os.path.abspath(output_save_dir)}")
    
    if final_output_files:
        print(f"Созданные файлы:")
        for file_path in final_output_files:
            print(f"  - {file_path}")
        return True, final_output_files, ""
    else:
        error_msg = "Не удалось создать ни одного выходного файла"
        print(error_msg)
        return False, [], error_msg


# --- Пример использования ---
if __name__ == "__main__":
    # Замените "test.mp4" на путь к вашему видео
    # Можно переопределить любые параметры здесь
    process_video_for_speaker_cuts(
        input_video_path="test.mp4", 
        output_save_dir="./my_speaker_cuts_test/",
        # autodetect_speaker_count=False, # Пример отключения автодетекции
        # top_n_faces_to_crop=1,        # и использования ручного выбора
        add_audio_to_output=True,
        analysis_max_width=320 # Для более быстрого теста на слабом ПК
    )

    # Пример с другим видео и другими настройками:
    # process_video_for_speaker_cuts(
    #     input_video_path="another_video.mov",
    #     output_save_dir="./other_cuts/",
    #     fps_to_process_analysis=0.5, # Анализировать каждые 2 секунды
    #     min_track_duration_seconds=10,
    #     autodetect_max_speakers=2,
    #     cam_output_width=1280,
    #     cam_output_height=720,
    #     add_audio_to_output=True
    # )