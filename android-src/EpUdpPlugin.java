package dk.energipilot.app;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.SocketTimeoutException;
import java.nio.charset.StandardCharsets;

/**
 * UDP for the device drivers (KEBA answers on port 7090). A WebView can't send UDP, so the app does it here:
 * send one message, wait for the answer from that host. One request at a time (KEBA wants that anyway).
 */
@CapacitorPlugin(name = "EpUdp")
public class EpUdpPlugin extends Plugin {
    private final Object lock = new Object();

    @PluginMethod
    public void request(PluginCall call) {
        final String host = call.getString("host");
        final int port = call.getInt("port", 7090);
        final String message = call.getString("message", "");
        final int timeoutMs = call.getInt("timeoutMs", 3000);
        final int localPort = call.getInt("localPort", 0);
        final boolean expectReply = call.getBoolean("expectReply", true);
        if (host == null || host.isEmpty()) {
            call.reject("host mangler");
            return;
        }
        new Thread(() -> {
            synchronized (lock) {
                DatagramSocket socket = null;
                try {
                    socket = new DatagramSocket(null);
                    socket.setReuseAddress(true);
                    socket.bind(new InetSocketAddress(localPort));
                    InetAddress addr = InetAddress.getByName(host);
                    byte[] data = message.getBytes(StandardCharsets.UTF_8);
                    socket.send(new DatagramPacket(data, data.length, addr, port));
                    JSObject ret = new JSObject();
                    if (!expectReply) {
                        call.resolve(ret);
                        return;
                    }
                    long end = System.currentTimeMillis() + timeoutMs;
                    byte[] buf = new byte[8192];
                    while (true) {
                        int left = (int) (end - System.currentTimeMillis());
                        if (left <= 0) {
                            call.reject("intet svar fra " + host);
                            return;
                        }
                        socket.setSoTimeout(left);
                        DatagramPacket packet = new DatagramPacket(buf, buf.length);
                        socket.receive(packet);
                        if (packet.getAddress().equals(addr)) {
                            ret.put("reply", new String(packet.getData(), 0, packet.getLength(), StandardCharsets.UTF_8));
                            call.resolve(ret);
                            return;
                        }
                    }
                } catch (SocketTimeoutException e) {
                    call.reject("intet svar fra " + host);
                } catch (Exception e) {
                    call.reject(e.getMessage() == null ? e.toString() : e.getMessage());
                } finally {
                    if (socket != null) socket.close();
                }
            }
        }).start();
    }
}
