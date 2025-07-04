import pickle
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
import cv2
import os
import numpy as np
import settings
import math
import argparse
from settings import CALIB_FILE_NAME, PERSPECTIVE_FILE_NAME



def get_center_shift(coeffs, img_size, pixels_per_meter):
    return np.polyval(coeffs, img_size[1]/pixels_per_meter[1]) - (img_size[0]//2)/pixels_per_meter[0]

def get_curvature(coeffs, img_size, pixels_per_meter):
    denom = np.abs(2 * coeffs[0])
    if denom < 1e-5:  # prevent divide-by-zero or near-zero
        return float('inf')
    y_eval = img_size[1] / pixels_per_meter[1]
    return ((1 + (2 * coeffs[0] * y_eval + coeffs[1]) ** 2) ** 1.5) / denom

class LaneLineFinder:
    def __init__(self, img_size, pixel_Per_Metter, center_shift):
        self.found = False
        self.poly_coeffs = np.zeros(3, dtype=np.float32)
        self.coeffecient_matrix_history = np.zeros((3, 7), dtype=np.float32)
        self.img_size = img_size
        self.pixel_Per_Metter = pixel_Per_Metter
        self.line_mask = np.ones((img_size[1], img_size[0]), dtype=np.uint8)
        self.other_line_mask = np.zeros_like(self.line_mask)
        self.line = np.zeros_like(self.line_mask)
        self.num_lost = 0
        self.still_to_find = 1
        self.shift = center_shift
        self.first = True
        self.stddev = 0
        
    def lane_setting(self):
        self.found = False
        self.poly_coeffs = np.zeros(3, dtype=np.float32)
        self.line_mask[:] = 1
        self.first = True

    def loss_lane(self):
        self.still_to_find = 5
        if self.found:
            self.num_lost += 1
            if self.num_lost >= 7:
                self.lane_setting()

    def firs_lane_found(self):
        self.first = False
        self.num_lost = 0
        if not self.found:
            self.still_to_find -= 1
            if self.still_to_find <= 0:
                self.found = True

    def polyfitting(self, mask):
        cord_y, x_coord = np.where(mask)
        cord_y = cord_y.astype(np.float32)/self.pixel_Per_Metter[1]
        x_coord = x_coord.astype(np.float32)/self.pixel_Per_Metter[0]
        if len(cord_y) <= 150:
            matrix_coefficient = np.array([0, 0, (self.img_size[0]//2)/self.pixel_Per_Metter[0] + self.shift], dtype=np.float32)
        else:
            matrix_coefficient, v = np.polyfit(cord_y, x_coord, 2, rcond=1e-16, cov=True)
            self.stddev = 1 - math.exp(-5*np.sqrt(np.trace(v)))

        self.coeffecient_matrix_history = np.roll(self.coeffecient_matrix_history, 1)

        if self.first:
            self.coeffecient_matrix_history = np.reshape(np.repeat(matrix_coefficient, 7), (3, 7))
        else:
            self.coeffecient_matrix_history[:, 0] = matrix_coefficient

        value_x = get_center_shift(matrix_coefficient, self.img_size, self.pixel_Per_Metter)
        curve = get_curvature(matrix_coefficient, self.img_size, self.pixel_Per_Metter)

        if (self.stddev > 0.95) | (len(cord_y) < 150) | (math.fabs(value_x - self.shift) > math.fabs(0.5*self.shift)) \
                | (curve < 30):

            self.coeffecient_matrix_history[0:2, 0] = 0
            self.coeffecient_matrix_history[2, 0] = (self.img_size[0]//2)/self.pixel_Per_Metter[0] + self.shift
            self.loss_lane()
        else:
            self.firs_lane_found()

        self.poly_coeffs = np.mean(self.coeffecient_matrix_history, axis=1)

    def get_line_points(self):
        y = np.array(range(0, self.img_size[1]+1, 10), dtype=np.float32)/self.pixel_Per_Metter[1]
        x = np.polyval(self.poly_coeffs, y)*self.pixel_Per_Metter[0]
        y *= self.pixel_Per_Metter[1]
        return np.array([x, y], dtype=np.int32).T

    def get_other_line_points(self):
        pts = self.get_line_points()
        pts[:, 0] = pts[:, 0] - 2*self.shift*self.pixel_Per_Metter[0]
        return pts

    def find_lane_line(self, mask, reset=False):
        n_segments = 16
        window_width = 30
        step = self.img_size[1]//n_segments

        if reset or (not self.found and self.still_to_find == 5) or self.first:
            self.line_mask[:] = 0
            n_steps = 4
            window_start = self.img_size[0]//2 + int(self.shift*self.pixel_Per_Metter[0]) - 3 * window_width
            window_end = window_start + 6*window_width
            sm = np.sum(mask[self.img_size[1]-4*step:self.img_size[1], window_start:window_end], axis=0)
            sm = np.convolve(sm, np.ones((window_width,))/window_width, mode='same')
            argmax = window_start + np.argmax(sm)
            shift = 0
            for last in range(self.img_size[1], 0, -step):
                first_line = max(0, last - n_steps*step)
                sm = np.sum(mask[first_line:last, :], axis=0)
                sm = np.convolve(sm, np.ones((window_width,))/window_width, mode='same')
                window_start = min(max(argmax + int(shift)-window_width//2, 0), self.img_size[0]-1)
                window_end = min(max(argmax + int(shift) + window_width//2, 0+1), self.img_size[0])
                new_argmax = window_start + np.argmax(sm[window_start:window_end])
                new_max = np.max(sm[window_start:window_end])
                if new_max <= 2:
                    new_argmax = argmax + int(shift)
                    shift = shift/2
                if last != self.img_size[1]:
                    shift = shift*0.25 + 0.75*(new_argmax - argmax)
                argmax = new_argmax
                cv2.rectangle(self.line_mask, (argmax-window_width//2, last-step), (argmax+window_width//2, last),
                              1, thickness=-1)
        else:
            self.line_mask[:] = 0
            points = self.get_line_points()
            if not self.found:
                factor = 3
            else:
                factor = 2
            cv2.polylines(self.line_mask, [points], 0, 1, thickness=int(factor*window_width))
            
        self.polyfitting(self.line)    
        self.line = self.line_mask * mask
        
        self.first = False
        if not self.found:
            self.line_mask[:] = 1
        points = self.get_other_line_points()
        self.other_line_mask[:] = 0
        cv2.polylines(self.other_line_mask, [points], 0, 1, thickness=int(5*window_width))



class LaneFinder:
    def __init__(self):
        with open(CALIB_FILE_NAME, 'rb') as f:
            calib_data = pickle.load(f)
        self.cam_matrix = calib_data["cam_matrix"]
        self.coefficient_distance_matrix = calib_data["dist_coeffs"]

        with open(PERSPECTIVE_FILE_NAME, 'rb') as f:
            perspective_data = pickle.load(f)

        perspective_transform = perspective_data["perspective_transform"]
        pixels_per_meter = perspective_data['pixels_per_meter']

        self.found = False
        self.img_size = settings.ORIGINAL_SIZE
        self.warped_size = settings.UNWARPED_SIZE
        self.mask = np.zeros((self.warped_size[1], self.warped_size[0], 3), dtype=np.uint8)
        self.roi_mask = np.ones((self.warped_size[1], self.warped_size[0], 3), dtype=np.uint8)
        self.total_mask = np.zeros_like(self.roi_mask)
        self.warped_mask = np.zeros((self.warped_size[1], self.warped_size[0]), dtype=np.uint8)
        self.M = perspective_transform
        self.count = 0
        self.line_l = LaneLineFinder(self.warped_size, pixels_per_meter, -1.8288)  
        self.line_r = LaneLineFinder(self.warped_size, pixels_per_meter, 1.8288)
        self.warning_icon=np.array(mpimg.imread('warning.png')*255, dtype=np.uint8)

    def undistort(self, img):
        return cv2.undistort(img, self.cam_matrix, self.coefficient_distance_matrix)

    def warp(self, img):
        return cv2.warpPerspective(img, self.M, self.warped_size, flags=cv2.WARP_FILL_OUTLIERS+cv2.INTER_CUBIC)

    def unwarp_img(self, img):
        return cv2.warpPerspective(img, self.M, self.img_size, flags=cv2.WARP_FILL_OUTLIERS +
                                                                     cv2.INTER_CUBIC+cv2.WARP_INVERSE_MAP)

    def line_equalization(self, alpha=0.9):
        mean = 0.5 * (self.line_l.coeffecient_matrix_history[:, 0] + self.line_r.coeffecient_matrix_history[:, 0])
        self.line_l.coeffecient_matrix_history[:, 0] = alpha * self.line_l.coeffecient_matrix_history[:, 0] + \
                                             (1-alpha)*(mean - np.array([0,0, 1.8288], dtype=np.uint8))
        self.line_r.coeffecient_matrix_history[:, 0] = alpha * self.line_r.coeffecient_matrix_history[:, 0] + \
                                              (1-alpha)*(mean + np.array([0,0, 1.8288], dtype=np.uint8))

    def find_lane(self, img, distorted=True, reset=False):
       
        if distorted:
            img = self.undistort(img)
        if reset:
            self.line_l.lane_setting()
            self.line_r.lane_setting()

        img = self.warp(img)
        img_hls = cv2.cvtColor(img, cv2.COLOR_RGB2HLS)
        img_hls = cv2.medianBlur(img_hls, 5)
        img_lab = cv2.cvtColor(img, cv2.COLOR_RGB2LAB)
        img_lab = cv2.medianBlur(img_lab, 5)

        big_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31))
        small_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))

        greenery = (img_lab[:, :, 2].astype(np.uint8) > 130) & cv2.inRange(img_hls, (0, 0, 50), (35, 190, 255))

        road_mask = np.logical_not(greenery).astype(np.uint8) & (img_hls[:, :, 1] < 250)
        road_mask = cv2.morphologyEx(road_mask, cv2.MORPH_OPEN, small_kernel)
        road_mask = cv2.dilate(road_mask, big_kernel)

        contours, hierarchy = cv2.findContours(road_mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)

        biggest_area = 0
        for contour in contours:
            area = cv2.contourArea(contour)
            if area>biggest_area:
                biggest_area = area
                biggest_contour = contour
        road_mask = np.zeros_like(road_mask)
        cv2.fillPoly(road_mask, [biggest_contour],  1)

        self.roi_mask[:, :, 0] = (self.line_l.line_mask | self.line_r.line_mask) & road_mask
        self.roi_mask[:, :, 1] = self.roi_mask[:, :, 0]
        self.roi_mask[:, :, 2] = self.roi_mask[:, :, 0]

        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 3))
        black = cv2.morphologyEx(img_lab[:,:, 0], cv2.MORPH_TOPHAT, kernel)
        lanes = cv2.morphologyEx(img_hls[:,:,1], cv2.MORPH_TOPHAT, kernel)

        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (13, 13))
        lanes_yellow = cv2.morphologyEx(img_lab[:, :, 2], cv2.MORPH_TOPHAT, kernel)

        self.mask[:, :, 0] = cv2.adaptiveThreshold(black, 1, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 13, -6)
        self.mask[:, :, 1] = cv2.adaptiveThreshold(lanes, 1, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 13, -4)
        self.mask[:, :, 2] = cv2.adaptiveThreshold(lanes_yellow, 1, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY,
                                                   13, -1.5)
        self.mask *= self.roi_mask
        small_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        self.total_mask = np.any(self.mask, axis=2).astype(np.uint8)
        self.total_mask = cv2.morphologyEx(self.total_mask.astype(np.uint8), cv2.MORPH_ERODE, small_kernel)

        left_mask = np.copy(self.total_mask)
        right_mask = np.copy(self.total_mask)
        if self.line_r.found:
            left_mask = left_mask & np.logical_not(self.line_r.line_mask) & self.line_r.other_line_mask
        if self.line_l.found:
            right_mask = right_mask & np.logical_not(self.line_l.line_mask) & self.line_l.other_line_mask
        self.line_l.find_lane_line(left_mask, reset)
        self.line_r.find_lane_line(right_mask, reset)
        self.found = self.line_l.found and self.line_r.found

        if self.found:
            self.line_equalization(0.875)
#for better visualization i have used the other comptetioner visualization works to have good output.
    def lane_draw(self, img, thickness=6, alpha=0.8, beta=1, gamma=0):
        line_l = self.line_l.get_line_points()
        line_r = self.line_r.get_line_points()
        both_lines = np.concatenate((line_l, np.flipud(line_r)), axis=0)
        lanes = np.zeros((self.warped_size[1], self.warped_size[0], 3), dtype=np.uint8)
        if self.found:
            cv2.fillPoly(lanes, [both_lines.astype(np.int32)], (128,0,128))
            cv2.polylines(lanes, [line_l.astype(np.int32)], False, (255, 0, 0),thickness=6 )
            cv2.polylines(lanes, [line_r.astype(np.int32)],False,  (0, 0, 255), thickness=6)
            cv2.fillPoly(lanes, [both_lines.astype(np.int32)], (128,60,128))
            mid_coef = 0.5 * (self.line_l.poly_coeffs + self.line_r.poly_coeffs)
            curve = get_curvature(mid_coef, img_size=self.warped_size, pixels_per_meter=self.line_l.pixel_Per_Metter)
            shift = get_center_shift(mid_coef, img_size=self.warped_size, pixels_per_meter=self.line_l.pixel_Per_Metter)

           
        else:
            warning_shape = self.warning_icon.shape
            corner = (10, (img.shape[1]-warning_shape[1])//2)
            patch = img[corner[0]:corner[0]+warning_shape[0], corner[1]:corner[1]+warning_shape[1]].copy()
            patch[self.warning_icon[:, :, 3] > 0] = self.warning_icon[self.warning_icon[:, :, 3] > 0, 0:3]
            img = np.array(img, copy=True)  # Make a writable copy
            img[corner[0]:corner[0]+warning_shape[0], corner[1]:corner[1]+warning_shape[1]]=patch
            cv2.putText(img, "Lane lost!", (550, 170), cv2.FONT_HERSHEY_PLAIN, fontScale=2.5,
                        thickness=5, color=(255, 255, 255))
            cv2.putText(img, "Lane lost!", (550, 170), cv2.FONT_HERSHEY_PLAIN, fontScale=2.5,
                        thickness=3, color=(0, 0, 0))
        lanes_unwarped = self.unwarp_img(lanes)
        return cv2.addWeighted(img, alpha, lanes_unwarped, beta, gamma)


    def process_image(self, img, reset=False, show_period=10, block=False):
        self.find_lane(img, reset=reset)
        lane_img = self.lane_draw(img)
        self.count += 1

        # Compute offset
        offset = 0.0
        if self.found:
            mid_coef = 0.5 * (self.line_l.poly_coeffs + self.line_r.poly_coeffs)
            offset = get_center_shift(
                mid_coef,
                img_size=self.warped_size,
                pixels_per_meter=self.line_l.pixel_Per_Metter
            )
        else: 
            offset = 0.0


        if show_period > 0 and (self.count % show_period == 1 or show_period == 1):
            start = 231
            plt.clf()
            for i in range(3):
                plt.subplot(start + i)
                plt.imshow(self.mask[:, :, i] * 255, cmap='gray')
                plt.subplot(234)
            plt.imshow((self.line_l.line + self.line_r.line) * 255)

            ll = cv2.merge((self.line_l.line, self.line_l.line * 0, self.line_r.line))
            lm = cv2.merge((self.line_l.line_mask, self.line_l.line * 0, self.line_r.line_mask))
            plt.subplot(235)
            plt.imshow(self.roi_mask * 255, cmap='gray')
            plt.subplot(236)
            plt.imshow(lane_img)
            if block:
                plt.show()
            else:
                plt.draw()
                plt.pause(0.000001)

        return cv2.cvtColor(lane_img, cv2.COLOR_RGB2BGR), -offset, self.found 



import argparse

def get_args():
    parser = argparse.ArgumentParser(description="Lane Detection from video or webcam.")
    parser.add_argument('--input', required=True, help='Path to video file or camera index (e.g., 0)')
    return parser.parse_args()


def main():
    args = get_args()

    # Use camera index or video path
    input_source = int(args.input) if args.input.isdigit() else args.input
    cap = cv2.VideoCapture(input_source)
    if not cap.isOpened():
        print(f"Failed to open video source: {args.input}")
        return

    lane_finder = LaneFinder()

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break

        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        output, offset, _ = lane_finder.process_image(frame_rgb, reset=False, show_period=0)

        cv2.imshow("Lane Detection", output)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
