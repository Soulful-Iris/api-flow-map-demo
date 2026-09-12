package com.acme.orders.api;
import com.acme.orders.domain.Order;
import org.springframework.stereotype.Component;
import software.amazon.awssdk.services.s3.S3Client;

@Component
public class ReceiptRenderer {
    private final S3Client s3Client;
    public ReceiptRenderer(S3Client s3Client) { this.s3Client = s3Client; }
    public byte[] render(Order order) {
        byte[] template = s3Client.getObjectAsBytes(b -> b.bucket("receipts").key("template.pdf")).asByteArray();
        return template;
    }
}
