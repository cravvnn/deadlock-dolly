#pragma once
// Bounded ordered writer for captured sequence frames. Completed readbacks are
// handed off in capture order and flushed by one worker. A full queue or a
// failed write is reported to the caller; memory never grows past the bound.
#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <cstring>
#include <deque>
#include <fstream>
#include <mutex>
#include <string>
#include <thread>
#include <vector>
namespace dolly::player_capture {
class SequenceWriter {
public:
    using Buffer = std::vector<unsigned char>;
    SequenceWriter() = default;
    SequenceWriter(const SequenceWriter&) = delete;
    SequenceWriter& operator=(const SequenceWriter&) = delete;
    ~SequenceWriter() { finish(); }

    bool open(const std::wstring& path, unsigned maxQueued) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (opened_ && !closed_) return false;
        queue_.clear(); free_.clear();
        maxQueued_ = maxQueued ? maxQueued : 1;
        file_.open(path, std::ios::binary | std::ios::trunc);
        if (!file_) return false;
        opened_ = true; closed_ = false; closing_ = false;
        error_.store(false); written_.store(0); refused_.store(0); inFlight_.store(0);
        if (!synchronous_) worker = std::thread([this] { run(); });
        return true;
    }

    void synchronous_for_tests(bool enable) { synchronous_ = enable; }
    void hold_worker_for_tests(bool hold) {
        { std::lock_guard<std::mutex> lock(holdMutex_); hold_ = hold; }
        holdCv_.notify_all();
    }
    unsigned written() const { return written_.load(); }
    unsigned refused() const { return refused_.load(); }
    unsigned in_flight() const { return inFlight_.load(); }
    bool error() const { return error_.load(); }

    Buffer take_buffer(std::size_t bytes) {
        Buffer buffer;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            if (!free_.empty()) { buffer = std::move(free_.back()); free_.pop_back(); }
        }
        buffer.resize(bytes);
        return buffer;
    }

    // False means the frame was not accepted (overflow, closed, or write failure).
    bool push(const std::uint64_t header[8], Buffer&& pixels) {
        if (!opened_ || closing_) return false;
        if (synchronous_) {
            const bool ok = write_record(header, pixels);
            if (ok) written_.fetch_add(1); else error_.store(true);
            recycle(std::move(pixels));
            return ok;
        }
        std::unique_lock<std::mutex> lock(mutex_);
        if (queue_.size() >= maxQueued_) {
            refused_.fetch_add(1);
            lock.unlock();
            recycle(std::move(pixels));
            return false;
        }
        queue_.emplace_back();
        std::memcpy(queue_.back().header, header, sizeof(queue_.back().header));
        queue_.back().pixels = std::move(pixels);
        wake_.notify_one();
        return true;
    }

    // Drains accepted frames and closes the file. Returns false on any write
    // failure; the caller must also check refused() for rejected frames.
    bool finish() {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            if (!opened_ || closed_) return opened_ && !error_.load();
            closing_ = true;
            wake_.notify_all();
        }
        hold_worker_for_tests(false);
        if (worker.joinable()) worker.join();
        std::lock_guard<std::mutex> lock(mutex_);
        queue_.clear();
        if (file_.is_open()) {
            file_.flush();
            if (!file_) error_.store(true);
            file_.close();
        }
        closed_ = true;
        return !error_.load();
    }

private:
    struct Item { std::uint64_t header[8]{}; Buffer pixels; };
    bool write_record(const std::uint64_t header[8], const Buffer& pixels) {
        file_.write(reinterpret_cast<const char*>(header), static_cast<std::streamsize>(sizeof(std::uint64_t) * 8));
        if (!pixels.empty())
            file_.write(reinterpret_cast<const char*>(pixels.data()), static_cast<std::streamsize>(pixels.size()));
        return bool(file_);
    }
    void recycle(Buffer&& buffer) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (free_.size() < maxQueued_ + 2) free_.push_back(std::move(buffer));
    }
    void run() {
        for (;;) {
            Item item;
            {
                std::unique_lock<std::mutex> lock(mutex_);
                wake_.wait(lock, [this] { return closing_ || !queue_.empty(); });
                if (queue_.empty()) { if (closing_) return; continue; }
                item = std::move(queue_.front());
                queue_.pop_front();
            }
            inFlight_.fetch_add(1);
            {
                std::unique_lock<std::mutex> hold(holdMutex_);
                holdCv_.wait(hold, [this] { return !hold_; });
            }
            if (!error_.load() && !write_record(item.header, item.pixels)) error_.store(true);
            if (!error_.load()) written_.fetch_add(1);
            inFlight_.fetch_sub(1);
            recycle(std::move(item.pixels));
        }
    }
    std::ofstream file_;
    std::thread worker;
    std::mutex mutex_;
    std::condition_variable wake_;
    std::mutex holdMutex_;
    std::condition_variable holdCv_;
    std::deque<Item> queue_;
    std::vector<Buffer> free_;
    std::atomic<unsigned> written_{0}, refused_{0}, inFlight_{0};
    std::atomic<bool> error_{false};
    unsigned maxQueued_ = 1;
    bool opened_ = false, closed_ = false, closing_ = false, synchronous_ = false, hold_ = false;
};
}
